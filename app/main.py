from __future__ import annotations

import ipaddress
import html
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.wsgi import WSGIMiddleware

from .auth import COOKIE_NAME, parse_user, sign_user
from .config import ProxySettings, load_settings, save_settings
from .dash_app import create_dash_app
from .db import (
    ensure_default_proxy_group,
    init_db,
    list_proxy_groups,
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
        group_ids = {int(group["id"]) for group in groups}
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
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/ui", WSGIMiddleware(create_dash_app().server), name="ui")


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


def _ip_allowed(request: Request) -> bool:
    settings = load_settings(SETTINGS_PATH)
    if not (settings.allowed_client_ips or "").strip():
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


@app.middleware("http")
async def _web_access_middleware(request: Request, call_next):
    if request.url.path.startswith("/ui"):
        if not _ip_allowed(request):
            return JSONResponse(status_code=403, content={"detail": "Forbidden: IP not in allowlist"})
        if not request.cookies.get(COOKIE_NAME):
            return RedirectResponse(url="/login", status_code=303)

        username = parse_user(request.cookies.get(COOKIE_NAME))
        if not username:
            response = RedirectResponse(url="/login", status_code=303)
            response.delete_cookie(COOKIE_NAME)
            return response
    response = await call_next(request)
    return response


def _safe_error(error: str | None) -> str:
    return html.escape((error or "").strip())


def _render_login_page(next_url: str = "/ui/", error: str | None = None) -> str:
    err_text = _safe_error(error)
    err_html = f'<div class="error">{err_text}</div>' if err_text else ""
    return f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>代理管理登录</title>
  <style>
    :root {{
      --bg: radial-gradient(120deg, #0e1f3a 0%, #0a1325 55%, #060d18 100%);
      --card: #12213a;
      --text: #e7f0ff;
      --muted: #93a4bf;
      --line: #284065;
      --primary: #5c8cff;
    }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: var(--bg);
      color: var(--text);
      font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
    }}
    .box {{
      width: min(460px, 92vw);
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 28px;
      box-shadow: 0 16px 40px rgba(0, 0, 0, 0.35);
    }}
    .title {{
      margin: 0 0 12px;
      letter-spacing: 0.5px;
      font-size: 1.3rem;
    }}
    .hint {{ color: var(--muted); margin-bottom: 16px; }}
    .field {{ margin-bottom: 14px; display: grid; gap: 8px; }}
    label {{ font-size: 14px; }}
    input {{
      background: #0c1728;
      border: 1px solid var(--line);
      color: var(--text);
      border-radius: 10px;
      height: 38px;
      padding: 0 12px;
    }}
    button {{
      margin-top: 6px;
      width: 100%;
      border: 0;
      height: 40px;
      border-radius: 10px;
      color: #fff;
      background: linear-gradient(120deg, var(--primary), #7fd3ff);
      cursor: pointer;
      font-weight: 600;
    }}
    .error {{
      color: #ffb4c0;
      margin-bottom: 12px;
      background: rgba(255, 76, 112, 0.12);
      border-radius: 10px;
      border: 1px solid rgba(255, 76, 112, 0.35);
      padding: 8px 10px;
      font-size: 0.95rem;
    }}
  </style>
</head>
<body>
  <section class="box">
    <h1 class="title">域名代理网关</h1>
    <div class="hint">登录后即可管理域名代理与分组</div>
    {err_html}
    <form method="post" action="/login">
      <input type="hidden" name="next" value="{next_url}" />
      <div class="field">
        <label for="username">用户名</label>
        <input id="username" name="username" autocomplete="username" required />
      </div>
      <div class="field">
        <label for="password">密码</label>
        <input id="password" name="password" type="password" autocomplete="current-password" required />
      </div>
      <button type="submit">进入控制台</button>
    </form>
  </section>
</body>
</html>
"""


def _normalize_next_url(path: str | None) -> str:
    if not path or not str(path).startswith("/"):
        return "/ui/"
    return str(path)


@app.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    next: str = "/ui/",
    error: str | None = None,
    _=Depends(_require_web_access),
):
    if request.cookies.get(COOKIE_NAME):
        username = parse_user(request.cookies.get(COOKIE_NAME))
        if username:
            return RedirectResponse(url=_normalize_next_url(next), status_code=303)
    return _render_login_page(_normalize_next_url(next), error)


@app.post("/login")
async def do_login(
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next: str = Form("/ui/"),
    _: None = Depends(_require_web_access),
):
    name = (username or "").strip()
    pwd = password or ""
    if not name or not pwd or not verify_user(DB_PATH, name, pwd):
        return HTMLResponse(_render_login_page(_normalize_next_url(next), "用户名或密码错误"), status_code=401)
    response = RedirectResponse(url=_normalize_next_url(next), status_code=303)
    response.set_cookie(COOKIE_NAME, sign_user(name), httponly=True)
    return response


@app.get("/logout")
async def logout(_: None = Depends(_require_web_access)):
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


@app.get("/")
async def index():
    return RedirectResponse(url="/ui/", status_code=303)


@app.get("/health")
async def health():
    return JSONResponse({"ok": True})
