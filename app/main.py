from __future__ import annotations

import ipaddress
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .config import ProxySettings, load_settings, save_settings
from .db import (
    add_proxy_group,
    add_rule,
    batch_delete_rules,
    batch_move_rules,
    ensure_default_proxy_group,
    create_user,
    get_proxy_group,
    init_db,
    list_proxy_groups,
    list_rules,
    list_users,
    remove_proxy_group,
    remove_rule,
    update_proxy_group,
    update_user_password,
    user_exists,
    verify_user,
)
from .proxy import ProxyGateway


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "app.db"
SETTINGS_PATH = DATA_DIR / "settings.json"


def _normalize_proxy_mode(mode: str) -> str:
    value = (mode or "").strip().lower()
    if value in {"http", "socks5"}:
        return "single_ip"
    if value not in {"direct", "single_ip", "api", "bigdata_api"}:
        return "direct"
    return value


def _ensure_init_files() -> ProxySettings:
    init_db(DB_PATH)
    settings = load_settings(SETTINGS_PATH)
    settings.proxy_mode = _normalize_proxy_mode(settings.proxy_mode)
    changed = False

    # Keep existing one-click install compatibility: if db has no proxy group
    # yet, create one by migration from legacy settings fields.
    groups = list_proxy_groups(DB_PATH)
    if not groups:
        default_group_id = ensure_default_proxy_group(
            DB_PATH,
            mode=settings.proxy_mode,
            proxy_protocol=(settings.proxy_protocol or "http").strip() or "http",
            proxy_host=settings.proxy_host,
            proxy_port=settings.proxy_port,
            proxy_username=settings.proxy_username,
            proxy_password=settings.proxy_password,
            api_url=settings.api_url,
            api_method=(settings.api_method or "GET").upper(),
            api_timeout=settings.api_timeout,
            api_cache_ttl=settings.api_cache_ttl,
            api_headers=settings.api_headers,
            api_body=settings.api_body,
            api_host_key=settings.api_host_key,
            api_port_key=settings.api_port_key,
            api_username_key=settings.api_username_key,
            api_password_key=settings.api_password_key,
            api_proxy_field=settings.api_proxy_field,
            bigdata_api_url=settings.bigdata_api_url,
            bigdata_api_token=settings.bigdata_api_token,
        )
        settings.default_proxy_group_id = default_group_id
        changed = True
    else:
        group_ids = {int(g["id"]) for g in groups}
        default_group_id = int(settings.default_proxy_group_id or 1)
        if default_group_id not in group_ids:
            settings.default_proxy_group_id = min(group_ids)
            changed = True

    if changed:
        save_settings(SETTINGS_PATH, settings)

    return settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = _ensure_init_files()
    gateway = ProxyGateway(SETTINGS_PATH, DB_PATH)
    await gateway.start()
    app.state.settings = settings
    try:
        yield
    finally:
        await gateway.stop()


app = FastAPI(lifespan=lifespan)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def _load_secret() -> str:
    settings = load_settings(SETTINGS_PATH)
    return settings.session_secret or os.urandom(24).hex()


app.add_middleware(SessionMiddleware, secret_key=_load_secret())


def _session_secret() -> str:
    return load_settings(SETTINGS_PATH).session_secret


def _serialize_user(username: str) -> str:
    s = URLSafeTimedSerializer(_session_secret(), salt="pm-auth")
    return s.dumps({"u": username})


def _deserialize_user(token: str) -> str:
    s = URLSafeTimedSerializer(_session_secret(), salt="pm-auth")
    data = s.loads(token, max_age=3600 * 24)
    return str(data.get("u", ""))


def current_user(request: Request) -> str:
    token = request.cookies.get("auth")
    if not token:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    try:
        username = _deserialize_user(token)
    except (BadSignature, Exception):
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    if not username:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return username


def _settings_for_render(settings: ProxySettings, proxy_groups) -> dict:
    return {
        "listen_host": settings.listen_host,
        "listen_port": settings.listen_port,
        "web_host": settings.web_host,
        "web_port": settings.web_port,
        "allowed_client_ips": settings.allowed_client_ips,
        "default_proxy_group_id": int(settings.default_proxy_group_id or 1),
        "proxy_groups": proxy_groups,
    }


def _parse_optional_group_id(value: object, proxy_groups) -> int:
    if value is None:
        return 0
    group_id = _parse_int(value, default=0, minimum=0)
    if group_id <= 0:
        return 0

    for g in proxy_groups:
        if int(g["id"]) == group_id:
            return group_id
    return 0


def _group_rule_counts(rules) -> dict[int, int]:
    counts: dict[int, int] = {}
    for rule in rules:
        raw_gid = rule["group_id"] or 1
        gid = int(raw_gid)
        counts[gid] = counts.get(gid, 0) + 1
    return counts


def _normalize_rule_ids(values: list[str] | None) -> list[int]:
    if not values:
        return []

    ids = []
    seen: set[int] = set()
    for value in values:
        rid = _parse_int(value, default=0, minimum=1)
        if rid <= 0 or rid in seen:
            continue
        seen.add(rid)
        ids.append(rid)
    return ids


def _domains_query_url(active_rule_group: int) -> str:
    if active_rule_group > 0:
        return f"/?tab=domains&rule_group={active_rule_group}"
    return "/?tab=domains"


def _allowed_networks() -> list[ipaddress._BaseNetwork]:
    settings = load_settings(SETTINGS_PATH)
    raw = (settings.allowed_client_ips or "").strip()
    if not raw:
        return []

    entries = []
    for item in raw.replace(";", ",").replace("\n", ",").replace(" ", ",").split(","):
        token = item.strip()
        if not token:
            continue
        try:
            net = ipaddress.ip_network(token, strict=False)
            entries.append(net)
        except ValueError:
            try:
                ip = ipaddress.ip_address(token)
                prefix = 32 if ip.version == 4 else 128
                entries.append(ipaddress.ip_network(f"{ip}/{prefix}", strict=False))
            except ValueError:
                continue
    return entries


def _parse_int(
    value: object,
    default: int,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    if num < minimum:
        num = minimum
    if maximum is not None and num > maximum:
        num = maximum
    return num


def _parse_proxy_pool(raw: str) -> list[dict[str, str | int]]:
    entries: list[dict[str, str | int]] = []
    raw = (raw or "").strip()
    if not raw:
        return entries

    for line in raw.replace(";", "\n").split("\n"):
        item = line.strip()
        if not item:
            continue

        if "://" in item:
            parsed = _extract_proxy_url(item)
            if parsed is None:
                raise ValueError("single_ip_invalid_pool")
            entries.append(parsed)
            continue

        if ":" not in item:
            raise ValueError("single_ip_invalid_pool")

        host = ""
        port = 0
        username = ""
        password = ""
        parts = item.split(":")
        if item.startswith("["):
            end = item.find("]")
            if end <= 0:
                raise ValueError("single_ip_invalid_pool")
            host = item[1:end].strip()
            tail = item[end + 1 :].strip()
            if not tail.startswith(":"):
                raise ValueError("single_ip_invalid_pool")
            tail = tail[1:]
            main, _, _rest = tail.partition(":")
            port_part = main.strip()
            if port_part:
                port = _parse_int(port_part, default=0, minimum=1, maximum=65535)
                if port == 0:
                    raise ValueError("single_ip_invalid_pool")
            else:
                raise ValueError("single_ip_invalid_pool")
            if _rest:
                auth = _rest.split(":", 2)
                username = auth[0].strip()
                password = (auth[1].strip() if len(auth) > 1 else "").strip()
        else:
            tokens = item.split(":")
            if len(tokens) < 2:
                raise ValueError("single_ip_invalid_pool")
            host = tokens[0].strip()
            port = _parse_int(tokens[1].strip(), default=0, minimum=1, maximum=65535)
            if not host or port == 0:
                raise ValueError("single_ip_invalid_pool")
            if len(tokens) > 2:
                username = tokens[2].strip()
            if len(tokens) > 3:
                password = ":".join(tokens[3:]).strip()

        if not host or not port:
            raise ValueError("single_ip_invalid_pool")
        entries.append(
            {
                "host": host,
                "port": port,
                "username": username,
                "password": password,
            }
        )

    return entries


def _extract_proxy_url(raw: str) -> dict[str, str | int] | None:
    text = raw.strip()
    if not text:
        return None

    from urllib.parse import urlsplit

    target = text if "://" in text else f"http://{text}"
    parsed = urlsplit(target)
    if not parsed.hostname or not parsed.port:
        return None
    return {
        "host": parsed.hostname,
        "port": parsed.port,
        "username": parsed.username or "",
        "password": parsed.password or "",
    }


def _parse_bool(value: object) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    text = str(value).strip().lower()
    return 1 if text in {"1", "true", "on", "yes", "y"} else 0


def _ip_allowed(request: Request) -> bool:
    settings = load_settings(SETTINGS_PATH)
    raw = (settings.allowed_client_ips or "").strip()
    if not raw:
        return True

    if request.client is None or not request.client.host:
        return False

    try:
        client_ip = ipaddress.ip_address(request.client.host)
    except ValueError:
        return False

    for network in _allowed_networks():
        if client_ip in network:
            return True
    return False


def _require_web_access(request: Request):
    if not _ip_allowed(request):
        raise HTTPException(status_code=403, detail="Forbidden: IP not in allowlist")


def _normalize_group_fields(
    *,
    proxy_mode: str,
    proxy_protocol: str = "http",
    proxy_host: str = "",
    proxy_port: int | str = 0,
    proxy_username: str = "",
    proxy_password: str = "",
    api_url: str = "",
    api_method: str = "GET",
    api_timeout: int | str = 8,
    api_cache_ttl: int | str = 20,
    api_headers: str = "",
    api_body: str = "",
    api_host_key: str = "host",
    api_port_key: str = "port",
    api_username_key: str = "username",
    api_password_key: str = "password",
    api_proxy_field: str = "proxy",
    proxy_pool: str = "",
    proxy_round_robin: int | str = 0,
    bigdata_api_url: str = "",
    bigdata_api_token: str = "",
) -> dict:
    mode = _normalize_proxy_mode(proxy_mode)
    protocol = (proxy_protocol or "http").strip() or "http"
    timeout = _parse_int(api_timeout, default=8, minimum=1, maximum=120)
    ttl = _parse_int(api_cache_ttl, default=20, minimum=0, maximum=86400)
    port = _parse_int(proxy_port, default=0, minimum=0, maximum=65535)
    method = (api_method or "GET").strip().upper()
    if method not in {"GET", "POST", "PUT", "PATCH"}:
        method = "GET"

    if mode == "direct":
        protocol = "http"
    if mode == "single_ip":
        pool = (proxy_pool or "").strip()
        if not proxy_host.strip() and not pool:
            raise ValueError("single_ip_missing_host")
        if pool:
            for item in _parse_proxy_pool(pool):
                if not item["host"] or item["port"] <= 0:
                    raise ValueError("single_ip_invalid_pool")
    if mode == "api" and not api_url.strip():
        raise ValueError("api_missing_url")
    if mode == "bigdata_api" and not ((bigdata_api_url or "").strip() or (api_url or "").strip()):
        raise ValueError("bigdata_missing_url")

    return {
        "mode": mode,
        "protocol": protocol,
        "proxy_host": (proxy_host or "").strip(),
        "proxy_port": port,
        "proxy_username": (proxy_username or "").strip(),
        "proxy_password": proxy_password or "",
        "proxy_pool": (proxy_pool or "").strip(),
        "proxy_round_robin": 1 if _parse_bool(proxy_round_robin) else 0,
        "api_url": (api_url or "").strip(),
        "api_method": method,
        "api_timeout": timeout,
        "api_cache_ttl": ttl,
        "api_headers": (api_headers or "").strip(),
        "api_body": api_body or "",
        "api_host_key": (api_host_key or "host").strip(),
        "api_port_key": (api_port_key or "port").strip(),
        "api_username_key": (api_username_key or "username").strip(),
        "api_password_key": (api_password_key or "password").strip(),
        "api_proxy_field": (api_proxy_field or "proxy").strip(),
        "bigdata_api_url": (bigdata_api_url or "").strip(),
        "bigdata_api_token": (bigdata_api_token or "").strip(),
    }


def _normalize_group_id(value: object, proxy_groups) -> int:
    if value is None:
        raise ValueError("group_required")
    try:
        group_id = int(value)
    except (TypeError, ValueError):
        raise ValueError("group_invalid")
    for g in proxy_groups:
        if int(g["id"]) == group_id:
            return group_id
    raise ValueError("group_not_exist")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, _=Depends(_require_web_access)):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
async def do_login(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    _: None = Depends(_require_web_access),
):
    if not verify_user(DB_PATH, username, password):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "用户名或密码错误"},
            status_code=401,
        )
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie("auth", _serialize_user(username), httponly=True)
    return response


@app.get("/logout")
async def logout(_: None = Depends(_require_web_access)):
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("auth")
    return response


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    tab: str | None = None,
    _: None = Depends(_require_web_access),
    username: str = Depends(current_user),
):
    rules = list_rules(DB_PATH)
    proxy_groups = list_proxy_groups(DB_PATH)
    users = list_users(DB_PATH)
    settings = _settings_for_render(load_settings(SETTINGS_PATH), proxy_groups)
    active_tab = tab or request.query_params.get("tab") or "domains"
    active_rule_group = _parse_optional_group_id(
        request.query_params.get("rule_group"), proxy_groups
    )
    focus_proxy_group = _parse_optional_group_id(
        request.query_params.get("focus_group"), proxy_groups
    )
    group_rule_counts = _group_rule_counts(rules)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": username,
            "rules": rules,
            "proxy_groups": proxy_groups,
            "users": users,
            "settings": settings,
            "active_tab": active_tab,
            "query_error": request.query_params.get("error"),
            "active_rule_group": active_rule_group,
            "focus_proxy_group": focus_proxy_group,
            "group_rule_counts": group_rule_counts,
        },
    )


@app.post("/rules", status_code=303)
async def add_rule_view(
    pattern: Annotated[str, Form()],
    kind: Annotated[str, Form()],
    group_id: Annotated[int, Form()],
    _: None = Depends(_require_web_access),
    username: str = Depends(current_user),
):
    del username
    proxy_groups = list_proxy_groups(DB_PATH)
    pattern = pattern.strip()
    if not pattern:
        return RedirectResponse(url="/?tab=domains&error=pattern", status_code=303)
    if kind not in {"exact", "suffix", "keyword"}:
        return RedirectResponse(url="/?tab=domains&error=kind", status_code=303)
    try:
        gid = _normalize_group_id(group_id, proxy_groups)
    except ValueError:
        return RedirectResponse(url="/?tab=domains&error=group", status_code=303)
    add_rule(DB_PATH, pattern, kind, gid)
    return RedirectResponse(url=f"/?tab=domains&rule_group={gid}", status_code=303)


@app.post("/rules/{rid}/delete", status_code=303)
async def delete_rule(
    rid: int,
    current_group: int = 0,
    _: None = Depends(_require_web_access),
    username: str = Depends(current_user),
):
    del username
    remove_rule(DB_PATH, rid)
    return RedirectResponse(url=_domains_query_url(_parse_optional_group_id(current_group, list_proxy_groups(DB_PATH))), status_code=303)


@app.post("/rules/bulk/delete", status_code=303)
async def bulk_delete_rules(
    rule_ids: list[str] = Form(default=[]),
    current_group: int = Form(default=0),
    _: None = Depends(_require_web_access),
    username: str = Depends(current_user),
):
    del username
    ids = _normalize_rule_ids(rule_ids)
    if not ids:
        return RedirectResponse(
            url=f"{_domains_query_url(_parse_optional_group_id(current_group, list_proxy_groups(DB_PATH)))}&error=batch_no_selection",
            status_code=303,
        )
    batch_delete_rules(DB_PATH, ids)
    return RedirectResponse(url=_domains_query_url(_parse_optional_group_id(current_group, list_proxy_groups(DB_PATH))), status_code=303)


@app.post("/rules/bulk/move", status_code=303)
async def bulk_move_rules(
    rule_ids: list[str] = Form(default=[]),
    target_group: int = Form(default=0),
    current_group: int = Form(default=0),
    _: None = Depends(_require_web_access),
    username: str = Depends(current_user),
):
    del username
    proxy_groups = list_proxy_groups(DB_PATH)
    try:
        target = _normalize_group_id(target_group, proxy_groups)
    except ValueError:
        return RedirectResponse(
            url=f"{_domains_query_url(_parse_optional_group_id(current_group, proxy_groups))}&error=batch_target_invalid",
            status_code=303,
        )

    ids = _normalize_rule_ids(rule_ids)
    if not ids:
        return RedirectResponse(
            url=f"{_domains_query_url(_parse_optional_group_id(current_group, proxy_groups))}&error=batch_no_selection",
            status_code=303,
        )
    batch_move_rules(DB_PATH, ids, target)
    return RedirectResponse(url=_domains_query_url(_parse_optional_group_id(current_group, proxy_groups)), status_code=303)


@app.post("/proxy-groups", status_code=303)
async def create_proxy_group(
    name: Annotated[str, Form()],
    proxy_mode: Annotated[str, Form()],
    proxy_protocol: Annotated[str, Form()] = "http",
    proxy_host: Annotated[str, Form()] = "",
    proxy_port: Annotated[int, Form()] = 0,
    proxy_username: Annotated[str, Form()] = "",
    proxy_password: Annotated[str, Form()] = "",
    proxy_pool: Annotated[str, Form()] = "",
    proxy_round_robin: Annotated[str, Form()] = "",
    api_url: Annotated[str, Form()] = "",
    api_method: Annotated[str, Form()] = "GET",
    api_timeout: Annotated[int, Form()] = 8,
    api_cache_ttl: Annotated[int, Form()] = 20,
    api_headers: Annotated[str, Form()] = "",
    api_body: Annotated[str, Form()] = "",
    api_host_key: Annotated[str, Form()] = "host",
    api_port_key: Annotated[str, Form()] = "port",
    api_username_key: Annotated[str, Form()] = "username",
    api_password_key: Annotated[str, Form()] = "password",
    api_proxy_field: Annotated[str, Form()] = "proxy",
    bigdata_api_url: Annotated[str, Form()] = "",
    bigdata_api_token: Annotated[str, Form()] = "",
    _: None = Depends(_require_web_access),
    username: str = Depends(current_user),
):
    del username
    try:
        payload = _normalize_group_fields(
            proxy_mode=proxy_mode,
            proxy_protocol=proxy_protocol,
            proxy_host=proxy_host,
            proxy_port=proxy_port,
            proxy_username=proxy_username,
            proxy_password=proxy_password,
            proxy_pool=proxy_pool,
            proxy_round_robin=proxy_round_robin,
            api_url=api_url,
            api_method=api_method,
            api_timeout=api_timeout,
            api_cache_ttl=api_cache_ttl,
            api_headers=api_headers,
            api_body=api_body,
            api_host_key=api_host_key,
            api_port_key=api_port_key,
            api_username_key=api_username_key,
            api_password_key=api_password_key,
            api_proxy_field=api_proxy_field,
            bigdata_api_url=bigdata_api_url,
            bigdata_api_token=bigdata_api_token,
        )
    except ValueError as exc:
        return RedirectResponse(url=f"/?tab=proxies&error={exc}", status_code=303)

    if not name.strip():
        return RedirectResponse(url="/?tab=proxies&error=group_name", status_code=303)
    try:
        add_proxy_group(
            DB_PATH,
            name=name.strip(),
            proxy_mode=payload["mode"],
            proxy_protocol=payload["protocol"],
            proxy_host=payload["proxy_host"],
            proxy_port=payload["proxy_port"],
            proxy_username=payload["proxy_username"],
            proxy_password=payload["proxy_password"],
            proxy_pool=payload["proxy_pool"],
            proxy_round_robin=payload["proxy_round_robin"],
            api_url=payload["api_url"],
            api_method=payload["api_method"],
            api_timeout=payload["api_timeout"],
            api_cache_ttl=payload["api_cache_ttl"],
            api_headers=payload["api_headers"],
            api_body=payload["api_body"],
            api_host_key=payload["api_host_key"],
            api_port_key=payload["api_port_key"],
            api_username_key=payload["api_username_key"],
            api_password_key=payload["api_password_key"],
            api_proxy_field=payload["api_proxy_field"],
            bigdata_api_url=payload["bigdata_api_url"],
            bigdata_api_token=payload["bigdata_api_token"],
        )
    except sqlite3.IntegrityError:
        return RedirectResponse(url="/?tab=proxies&error=group_exists", status_code=303)
    return RedirectResponse(url="/?tab=proxies", status_code=303)


@app.post("/proxy-groups/{group_id}/update", status_code=303)
async def update_proxy_group_view(
    group_id: int,
    name: Annotated[str, Form()],
    proxy_mode: Annotated[str, Form()],
    proxy_protocol: Annotated[str, Form()] = "http",
    proxy_host: Annotated[str, Form()] = "",
    proxy_port: Annotated[int, Form()] = 0,
    proxy_username: Annotated[str, Form()] = "",
    proxy_password: Annotated[str, Form()] = "",
    proxy_pool: Annotated[str, Form()] = "",
    proxy_round_robin: Annotated[str, Form()] = "",
    api_url: Annotated[str, Form()] = "",
    api_method: Annotated[str, Form()] = "GET",
    api_timeout: Annotated[int, Form()] = 8,
    api_cache_ttl: Annotated[int, Form()] = 20,
    api_headers: Annotated[str, Form()] = "",
    api_body: Annotated[str, Form()] = "",
    api_host_key: Annotated[str, Form()] = "host",
    api_port_key: Annotated[str, Form()] = "port",
    api_username_key: Annotated[str, Form()] = "username",
    api_password_key: Annotated[str, Form()] = "password",
    api_proxy_field: Annotated[str, Form()] = "proxy",
    bigdata_api_url: Annotated[str, Form()] = "",
    bigdata_api_token: Annotated[str, Form()] = "",
    _: None = Depends(_require_web_access),
    username: str = Depends(current_user),
):
    del username
    existing = get_proxy_group(DB_PATH, group_id)
    if existing is None:
        return RedirectResponse(url="/?tab=proxies&error=group_not_found", status_code=303)

    try:
        payload = _normalize_group_fields(
            proxy_mode=proxy_mode,
            proxy_protocol=proxy_protocol,
            proxy_host=proxy_host,
            proxy_port=proxy_port,
            proxy_username=proxy_username,
            proxy_password=proxy_password,
            proxy_pool=proxy_pool,
            proxy_round_robin=proxy_round_robin,
            api_url=api_url,
            api_method=api_method,
            api_timeout=api_timeout,
            api_cache_ttl=api_cache_ttl,
            api_headers=api_headers,
            api_body=api_body,
            api_host_key=api_host_key,
            api_port_key=api_port_key,
            api_username_key=api_username_key,
            api_password_key=api_password_key,
            api_proxy_field=api_proxy_field,
            bigdata_api_url=bigdata_api_url,
            bigdata_api_token=bigdata_api_token,
        )
    except ValueError as exc:
        return RedirectResponse(url=f"/?tab=proxies&error={exc}", status_code=303)

    if not name.strip():
        return RedirectResponse(url="/?tab=proxies&error=group_name", status_code=303)

    try:
        updated = update_proxy_group(
            DB_PATH,
            group_id=group_id,
            name=name.strip(),
            proxy_mode=payload["mode"],
            proxy_protocol=payload["protocol"],
            proxy_host=payload["proxy_host"],
            proxy_port=payload["proxy_port"],
            proxy_username=payload["proxy_username"],
            proxy_password=payload["proxy_password"],
            proxy_pool=payload["proxy_pool"],
            proxy_round_robin=payload["proxy_round_robin"],
            api_url=payload["api_url"],
            api_method=payload["api_method"],
            api_timeout=payload["api_timeout"],
            api_cache_ttl=payload["api_cache_ttl"],
            api_headers=payload["api_headers"],
            api_body=payload["api_body"],
            api_host_key=payload["api_host_key"],
            api_port_key=payload["api_port_key"],
            api_username_key=payload["api_username_key"],
            api_password_key=payload["api_password_key"],
            api_proxy_field=payload["api_proxy_field"],
            bigdata_api_url=payload["bigdata_api_url"],
            bigdata_api_token=payload["bigdata_api_token"],
        )
    except sqlite3.IntegrityError:
        return RedirectResponse(url="/?tab=proxies&error=group_exists", status_code=303)
    if not updated:
        return RedirectResponse(url="/?tab=proxies&error=update_failed", status_code=303)
    return RedirectResponse(url="/?tab=proxies", status_code=303)


@app.post("/proxy-groups/{group_id}/delete", status_code=303)
async def delete_proxy_group(
    group_id: int,
    _: None = Depends(_require_web_access),
    username: str = Depends(current_user),
):
    del username
    if not remove_proxy_group(DB_PATH, group_id):
        return RedirectResponse(url="/?tab=proxies&error=group_in_use", status_code=303)
    return RedirectResponse(url="/?tab=proxies", status_code=303)


@app.post("/system", status_code=303)
async def update_system_settings(
    listen_host: Annotated[str, Form()],
    listen_port: Annotated[int, Form()],
    web_host: Annotated[str, Form()],
    web_port: Annotated[int, Form()],
    default_proxy_group_id: Annotated[int, Form()],
    allowed_client_ips: Annotated[str, Form()] = "",
    _: None = Depends(_require_web_access),
    username: str = Depends(current_user),
):
    del username
    settings = load_settings(SETTINGS_PATH)
    proxy_groups = list_proxy_groups(DB_PATH)

    default_group = _normalize_group_id(default_proxy_group_id, proxy_groups)
    settings.listen_host = listen_host.strip() or "0.0.0.0"
    settings.listen_port = _parse_int(listen_port, default=3128, minimum=1, maximum=65535)
    settings.web_host = web_host.strip() or "0.0.0.0"
    settings.web_port = _parse_int(web_port, default=8080, minimum=1, maximum=65535)
    settings.default_proxy_group_id = default_group
    settings.allowed_client_ips = allowed_client_ips.strip()
    save_settings(SETTINGS_PATH, settings)
    return RedirectResponse(url="/?tab=system", status_code=303)


@app.post("/users", status_code=303)
async def add_user(
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    _: None = Depends(_require_web_access),
    operator: str = Depends(current_user),
):
    del operator
    username = username.strip()
    if not username or not password:
        return RedirectResponse(url="/?tab=system&error=user_input", status_code=303)
    if user_exists(DB_PATH, username):
        return RedirectResponse(url="/?tab=system&error=user_exists", status_code=303)
    create_user(DB_PATH, username, password)
    return RedirectResponse(url="/?tab=system", status_code=303)


@app.post("/users/{user_id}/password", status_code=303)
async def change_password(
    user_id: int,
    new_password: Annotated[str, Form()],
    _: None = Depends(_require_web_access),
    operator: str = Depends(current_user),
):
    del operator
    if not new_password:
        return RedirectResponse(url="/?tab=system&error=pwd_empty", status_code=303)
    if not update_user_password(DB_PATH, user_id, new_password):
        return RedirectResponse(url="/?tab=system&error=user_not_found", status_code=303)
    return RedirectResponse(url="/?tab=system", status_code=303)


@app.get("/health")
async def health():
    return JSONResponse({"ok": True})
