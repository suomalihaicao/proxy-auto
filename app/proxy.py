from __future__ import annotations

import asyncio
import base64
import errno
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .db import get_all_rules_for_matcher, get_proxy_group
from .config import ProxySettings, load_settings


LOGGER = logging.getLogger("domain_proxy")


@dataclass
class _Endpoint:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    remote_host: str
    remote_port: int


@dataclass
class _UpstreamProxy:
    transport: str
    host: str
    port: int
    username: str = ""
    password: str = ""


class RuleMatcher:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def match(self, host: str | None) -> int | None:
        if not host:
            return None
        host = host.lower().strip().strip(".")
        for item in get_all_rules_for_matcher(self.db_path):
            pattern = str(item["pattern"]).lower().strip().strip(".")
            kind = item["kind"]
            if not pattern:
                continue
            if pattern == "*":
                return int(item["group_id"])

            if pattern.startswith("*."):
                suffix = pattern[2:]
                if kind == "keyword":
                    if suffix in host:
                        return int(item["group_id"])
                    continue
                if host == suffix or host.endswith("." + suffix):
                    return int(item["group_id"])
                continue

            if kind == "exact":
                if host == pattern:
                    return int(item["group_id"])
            elif kind == "suffix":
                if host == pattern or host.endswith("." + pattern):
                    return int(item["group_id"])
            elif kind == "keyword":
                if pattern in host:
                    return int(item["group_id"])
        return None


class ProxyGateway:
    def __init__(self, settings_path: Path, db_path: Path):
        self.settings_path = settings_path
        self.db_path = db_path
        self.server: asyncio.base_events.Server | None = None
        self.matcher = RuleMatcher(db_path)
        self._api_cache: dict[int, tuple[str, _UpstreamProxy, float]] = {}
        self._api_cache_lock = asyncio.Lock()
        self._single_proxy_pool_cache: dict[int, list[tuple[str, int, str, str]]] = {}
        self._single_proxy_pool_raw: dict[int, str] = {}
        self._single_proxy_rr_cursor: dict[int, int] = {}
        self._single_proxy_lock = asyncio.Lock()

    def _current_settings(self) -> ProxySettings:
        return load_settings(self.settings_path)

    async def start(self):
        settings = self._current_settings()
        try:
            self.server = await asyncio.start_server(
                self._handle_client,
                host=settings.listen_host,
                port=settings.listen_port,
            )
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                LOGGER.warning(
                    "proxy listen %s:%s unavailable, skipping startup: %s",
                    settings.listen_host,
                    settings.listen_port,
                    exc,
                )
                self.server = None
                return
            raise
        LOGGER.info("proxy listening on %s:%s", settings.listen_host, settings.listen_port)

    async def stop(self):
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    async def _handle_client(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        try:
            first_line = await asyncio.wait_for(client_reader.readline(), timeout=8.0)
            if not first_line:
                client_writer.close()
                return
            try:
                line = first_line.decode("utf-8", errors="ignore")
                method, target, _version = line.strip().split(" ", 2)
            except ValueError:
                await self._error_response(client_writer, 400, "Bad Request")
                return

            headers = await self._read_headers(client_reader)
            if method.upper() == "CONNECT":
                await self._handle_connect(method, target, headers, client_reader, client_writer)
                return

            await self._handle_http(
                method=method,
                target=target,
                headers=headers,
                client_reader=client_reader,
                client_writer=client_writer,
            )
        except asyncio.TimeoutError:
            await self._error_response(client_writer, 408, "Request Timeout")
        except Exception as e:
            LOGGER.exception("proxy error: %s", e)
            await self._error_response(client_writer, 502, "Bad Gateway")
        finally:
            if not client_writer.is_closing():
                client_writer.close()
                await client_writer.wait_closed()

    async def _handle_connect(
        self,
        method: str,
        target: str,
        headers: dict[str, tuple[str, str]],
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        del method
        host, port = self._split_host_port(target, 443)
        group_id = self.matcher.match(host)
        upstream = None

        try:
            if group_id is None:
                upstream = await self._connect_direct(host, port)
            else:
                group = get_proxy_group(self.db_path, group_id)
                upstream_spec = await self._resolve_upstream(group)
                if upstream_spec is None:
                    upstream = await self._connect_direct(host, port)
                else:
                    upstream = await self._connect_via_upstream(
                        upstream_spec, host, port, headers, is_connect=True
                    )
        except RuntimeError as exc:
            await self._error_response(client_writer, 500, f"{exc}")
            return

        if upstream is None:
            await self._error_response(
                client_writer, 502, "Failed to establish upstream connection"
            )
            return

        client_writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await client_writer.drain()
        await self._pipe_bidirectional(
            client_reader, client_writer, upstream.reader, upstream.writer
        )

    async def _handle_http(
        self,
        method: str,
        target: str,
        headers: dict[str, tuple[str, str]],
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        parsed = urllib.parse.urlsplit(target)
        host = parsed.hostname
        host_header = headers.get("host")[0] if headers.get("host") else None
        if host is None and host_header:
            host = self._clean_host_header(host_header)

        host, port = self._split_host_port(host_header or host, 80 if method != "CONNECT" else 443)
        if host is None:
            await self._error_response(client_writer, 400, "Bad Request")
            return

        group_id = self.matcher.match(host)
        out_path = parsed.path or "/"
        if parsed.query:
            out_path += f"?{parsed.query}"

        upstream: _Endpoint | None = None
        upstream_spec: _UpstreamProxy | None = None

        try:
            upstream_spec = None
            if group_id is not None:
                group = get_proxy_group(self.db_path, group_id)
                upstream_spec = await self._resolve_upstream(group)

            if upstream_spec is None:
                upstream = await self._connect_direct(host, port)
            else:
                upstream = await self._connect_via_upstream(
                    upstream_spec, host, port, headers, is_connect=False
                )
        except RuntimeError as exc:
            await self._error_response(client_writer, 500, f"{exc}")
            return

        if upstream is None:
            await self._error_response(
                client_writer, 502, "Failed to establish upstream connection"
            )
            return

        # direct or socks transport uses origin-form; HTTP transport needs absolute-form.
        if group_id is not None and (
            upstream_spec is None or upstream_spec.transport == "socks5"
        ):
            await self._forward_request_to_origin(
                method=method,
                request_target=out_path,
                headers=headers,
                host=host,
                port=port,
                client_reader=client_reader,
                client_writer=client_writer,
                upstream=upstream,
            )
            return

        force_absolute = bool(
            group_id is not None and upstream_spec is not None and upstream_spec.transport == "http"
        )
        await self._forward_request_to_origin(
            method=method,
            request_target=target,
            headers=headers,
            host=host,
            port=port,
            client_reader=client_reader,
            client_writer=client_writer,
            upstream=upstream,
            force_absolute=force_absolute,
        )

    async def _resolve_upstream(self, group_row) -> _UpstreamProxy | None:
        if group_row is None:
            return None

        mode = (group_row["proxy_mode"] or "").strip().lower()
        if mode in {"http", "socks5"}:
            mode = "single_ip"

        if mode == "direct":
            return None

        if mode == "single_ip":
            return await self._resolve_single_proxy_upstream(group_row)

        if mode == "api":
            return await self._resolve_api_upstream(group_row, use_bigdata=False)

        if mode == "bigdata_api":
            return await self._resolve_api_upstream(group_row, use_bigdata=True)

        raise RuntimeError(f"Unsupported proxy mode: {group_row['proxy_mode']}")

    async def _resolve_single_proxy_upstream(self, group_row) -> _UpstreamProxy:
        transport = self._normalize_transport(group_row["proxy_protocol"])
        group_id = int(group_row["id"])
        pool = self._load_single_proxy_pool(group_row)

        if not pool and (
            group_row["proxy_host"]
            and str(group_row["proxy_host"]).strip()
            and int(group_row["proxy_port"]) > 0
        ):
            return _UpstreamProxy(
                transport=transport,
                host=str(group_row["proxy_host"]).strip(),
                port=int(group_row["proxy_port"]),
                username=str(group_row["proxy_username"] or "").strip(),
                password=str(group_row["proxy_password"] or ""),
            )

        if not pool:
            raise RuntimeError("Single IP 配置缺失")

        round_robin = int(group_row["proxy_round_robin"] or 0) > 0
        if len(pool) == 1 or not round_robin:
            host, port, username, password = pool[0]
        else:
            async with self._single_proxy_lock:
                cursor = self._single_proxy_rr_cursor.get(group_id, -1) + 1
                cursor %= len(pool)
                self._single_proxy_rr_cursor[group_id] = cursor
            host, port, username, password = pool[cursor]
            self._single_proxy_pool_cache[group_id] = pool

        return _UpstreamProxy(
            transport=transport,
            host=host,
            port=port,
            username=username,
            password=password,
        )

    def _load_single_proxy_pool(self, group_row) -> list[tuple[str, int, str, str]]:
        group_id = int(group_row["id"])
        cache = self._single_proxy_pool_cache.get(group_id)
        raw = str(group_row["proxy_pool"] or "").strip()
        if not raw:
            self._single_proxy_pool_cache.pop(group_id, None)
            self._single_proxy_pool_raw.pop(group_id, None)
            return []

        if cache is not None and self._single_proxy_pool_raw.get(group_id) == raw:
            return cache

        entries: list[tuple[str, int, str, str]] = []
        for line in raw.replace(";", "\n").split("\n"):
            item = line.strip()
            if not item:
                continue
            parsed = self._parse_single_proxy_entry(item)
            if parsed is not None:
                entries.append(parsed)
                continue
            raise RuntimeError("Single IP 代理池配置不合法")

        self._single_proxy_pool_cache[group_id] = entries
        self._single_proxy_pool_raw[group_id] = raw
        if not entries:
            raise RuntimeError("Single IP 代理池配置为空")
        return entries

    @staticmethod
    def _parse_single_proxy_entry(raw: str) -> tuple[str, int, str, str] | None:
        item = raw.strip()
        if not item:
            return None

        if "://" in item:
            parsed = ProxyGateway._extract_proxy_from_url(item)
            if parsed is None:
                return None
            return parsed[0], parsed[1], parsed[2], parsed[3]

        if item.startswith("["):
            end = item.find("]")
            if end <= 0:
                return None
            host = item[1:end].strip()
            tail = item[end + 1 :].strip()
            if not tail.startswith(":"):
                return None
            port_part, _, auth_part = tail[1:].partition(":")
            if not port_part:
                return None
            try:
                port = int(port_part)
            except ValueError:
                return None
            if port <= 0 or port > 65535:
                return None
            username = ""
            password = ""
            if auth_part:
                username, _, password = auth_part.partition(":")
            return host, port, username, password

        parts = item.split(":")
        if len(parts) < 2:
            return None
        host = parts[0].strip()
        if not host:
            return None
        try:
            port = int(parts[1])
        except ValueError:
            return None
        if port <= 0 or port > 65535:
            return None
        username = parts[2].strip() if len(parts) > 2 else ""
        password = ":".join(parts[3:]).strip() if len(parts) > 3 else ""
        return host, port, username, password

    async def _resolve_api_upstream(
        self, group_row, use_bigdata: bool
    ) -> _UpstreamProxy:
        group_id = int(group_row["id"])
        cache_key = self._build_api_cache_key(group_row, use_bigdata)
        now = time.time()

        cached = self._api_cache.get(group_id)
        if cached is not None:
            cached_key, cached_upstream, cached_expired_at = cached
            if cached_key == cache_key and now < cached_expired_at:
                return cached_upstream

        async with self._api_cache_lock:
            now = time.time()
            cached = self._api_cache.get(group_id)
            if cached is not None:
                cached_key, cached_upstream, cached_expired_at = cached
                if cached_key == cache_key and now < cached_expired_at:
                    return cached_upstream

            api_url, method, headers, body = self._prepare_api_request(group_row, use_bigdata)
            text = await asyncio.to_thread(
                self._call_api_proxy,
                api_url,
                method,
                headers,
                body,
                max(1, int(group_row["api_timeout"])),
            )
            upstream = self._parse_api_response(text, group_row, use_bigdata)
            ttl = max(0, int(group_row["api_cache_ttl"]))
            expire_at = now + ttl if ttl > 0 else now
            self._api_cache[group_id] = (cache_key, upstream, expire_at)
            return upstream

    def _build_api_cache_key(self, group_row, use_bigdata: bool) -> str:
        if use_bigdata:
            return json.dumps(
                {
                    "mode": "bigdata_api",
                    "url": (group_row["bigdata_api_url"] or "").strip()
                    or (group_row["api_url"] or "").strip(),
                    "token": (group_row["bigdata_api_token"] or "").strip(),
                    "timeout": int(group_row["api_timeout"]),
                    "transport": self._normalize_transport(group_row["proxy_protocol"]),
                    "host_key": group_row["api_host_key"],
                    "port_key": group_row["api_port_key"],
                    "username_key": group_row["api_username_key"],
                    "password_key": group_row["api_password_key"],
                    "proxy_field": group_row["api_proxy_field"],
                    "headers": (group_row["api_headers"] or "").strip(),
                    "body": (group_row["api_body"] or "").strip(),
                },
                sort_keys=True,
                ensure_ascii=False,
            )

        return json.dumps(
            {
                "mode": "api",
                "url": (group_row["api_url"] or "").strip(),
                "method": (group_row["api_method"] or "GET").upper(),
                "timeout": int(group_row["api_timeout"]),
                "transport": self._normalize_transport(group_row["proxy_protocol"]),
                "host_key": group_row["api_host_key"],
                "port_key": group_row["api_port_key"],
                "username_key": group_row["api_username_key"],
                "password_key": group_row["api_password_key"],
                "proxy_field": group_row["api_proxy_field"],
                "headers": (group_row["api_headers"] or "").strip(),
                "body": (group_row["api_body"] or "").strip(),
            },
            sort_keys=True,
            ensure_ascii=False,
        )

    def _prepare_api_request(
        self, group_row, use_bigdata: bool
    ) -> tuple[str, str, dict[str, str], bytes | None]:
        if use_bigdata:
            base_url = (
                (group_row["bigdata_api_url"] or "").strip()
                or (group_row["api_url"] or "").strip()
            )
            if not base_url:
                raise RuntimeError("BigData API 地址为空")

            token = (group_row["bigdata_api_token"] or "").strip()
            if "{token}" in base_url and token:
                api_url = base_url.replace("{token}", urllib.parse.quote_plus(token))
            else:
                api_url = base_url
                if token and "token=" not in api_url and "api_key=" not in api_url:
                    joiner = "&" if "?" in api_url else "?"
                    api_url = f"{api_url}{joiner}token={urllib.parse.quote_plus(token)}"
        else:
            api_url = (group_row["api_url"] or "").strip()
            if not api_url:
                raise RuntimeError("API 地址为空")

        method = (group_row["api_method"] or "GET").strip().upper()
        if method not in {"GET", "POST", "PUT", "PATCH"}:
            method = "GET"

        headers: dict[str, str] = {}
        raw_headers = (group_row["api_headers"] or "").strip()
        if raw_headers:
            parsed_headers = self._parse_json_object(raw_headers)
            for k, v in parsed_headers.items():
                if isinstance(k, str) and v is not None:
                    headers[k] = str(v)

        if use_bigdata:
            token = (group_row["bigdata_api_token"] or "").strip()
            if token:
                headers.setdefault("Authorization", f"Bearer {token}")

        body = None
        raw_body = (group_row["api_body"] or "").strip()
        if method != "GET" and raw_body:
            if raw_body.startswith("{") or raw_body.startswith("["):
                body_json = self._parse_json_object(raw_body)
                body = json.dumps(body_json, ensure_ascii=False).encode("utf-8")
                headers.setdefault("Content-Type", "application/json; charset=utf-8")
            else:
                body = raw_body.encode("utf-8")

        return api_url, method, headers, body

    @staticmethod
    def _parse_json_object(raw: str) -> dict:
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _parse_json_text(raw: str) -> dict | list:
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed

    @staticmethod
    def _call_api_proxy(
        url: str,
        method: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout: int,
    ) -> str:
        request = urllib.request.Request(
            url=url,
            method=method,
            data=body,
            headers=headers,
        )
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            data = resp.read()
            charset = resp.headers.get_content_charset(failobj="utf-8")
            return data.decode(charset, errors="ignore")

    def _parse_api_response(
        self, text: str, group_row, use_bigdata: bool
    ) -> _UpstreamProxy:
        payload = self._parse_json_text(text)
        transport = self._normalize_transport(group_row["proxy_protocol"])

        upstream = None
        if isinstance(payload, dict):
            upstream = self._extract_upstream_from_json(
                payload,
                group_row["api_host_key"],
                group_row["api_port_key"],
                group_row["api_username_key"],
                group_row["api_password_key"],
                group_row["api_proxy_field"],
            )
        if upstream is None:
            upstream = self._extract_upstream_from_text(
                text,
                group_row["api_proxy_field"],
            )

        if upstream is None:
            if use_bigdata:
                raise RuntimeError("BigData API 返回不能解析到上游代理")
            raise RuntimeError("API 返回不能解析到上游代理")

        if use_bigdata and not upstream.host:
            raise RuntimeError("BigData API 返回 host 为空")

        upstream.transport = transport
        return upstream

    @staticmethod
    def _normalize_transport(transport: str) -> str:
        value = (transport or "").strip().lower()
        return value if value in {"http", "socks5"} else "http"

    def _extract_upstream_from_json(
        self,
        payload: dict,
        host_key: str,
        port_key: str,
        user_key: str,
        pass_key: str,
        proxy_field: str,
    ) -> _UpstreamProxy | None:
        host = self._extract_value(payload, proxy_field, [])
        port = None
        username = ""
        password = ""

        if isinstance(host, str):
            parsed = self._extract_proxy_from_url(host)
            if parsed:
                host, port, username, password = parsed

        if not host:
            host = self._extract_value(
                payload,
                host_key,
                [
                    "proxy.host",
                    "proxy",
                    "ip",
                    "server",
                    "server_host",
                    "host",
                    "ip_address",
                ],
            )
            if isinstance(host, dict):
                host = None

        if not port:
            raw_port = self._extract_value(
                payload,
                port_key,
                ["proxy.port", "server_port", "port", "local_port"],
            )
            if isinstance(raw_port, str) and raw_port.startswith("http"):
                parsed = self._extract_proxy_from_url(raw_port)
                if parsed:
                    host = parsed[0]
                    port = parsed[1]
            else:
                port = self._to_int(raw_port)

        if not username:
            username = (
                self._extract_value(
                    payload,
                    user_key,
                    ["proxy.username", "user", "login", "username"],
                    as_str=True,
                )
                or ""
            )

        if not password:
            password = (
                self._extract_value(
                    payload,
                    pass_key,
                    ["proxy.password", "pass", "passwd", "password"],
                    as_str=True,
                )
                or ""
            )

        if not host or not port:
            return None

        return _UpstreamProxy(
            transport="http",
            host=str(host),
            port=port,
            username=str(username),
            password=str(password),
        )

    def _extract_upstream_from_text(
        self, text: str, proxy_field: str
    ) -> _UpstreamProxy | None:
        del proxy_field
        pattern = (
            r"(?P<proxy>(?:[a-zA-Z][a-zA-Z0-9+.-]*://)?"
            r"(?:(?:[a-zA-Z0-9._-]+|\\[[0-9a-fA-F:]+\\]))"
            r":(?P<port>\d{1,5}))"
        )
        for match in re.finditer(pattern, text):
            value = match.group("proxy")
            if not value:
                continue
            parsed = self._extract_proxy_from_url(value)
            if parsed:
                return _UpstreamProxy("http", parsed[0], parsed[1], parsed[2], parsed[3])
        return None

    @staticmethod
    def _extract_value(
        payload: object, key: str, fallback_keys: list[str], as_str: bool = False
    ) -> str | int | None:
        candidates = []
        if key:
            candidates.append(key)
        candidates.extend(fallback_keys)

        for path in candidates:
            if not path:
                continue
            value: object = payload
            ok = True
            for part in path.split("."):
                if isinstance(value, dict):
                    if part not in value:
                        ok = False
                        break
                    value = value[part]
                elif isinstance(value, list) and part.isdigit():
                    idx = int(part)
                    if idx < 0 or idx >= len(value):
                        ok = False
                        break
                    value = value[idx]
                else:
                    ok = False
                    break
            if not ok:
                continue
            if as_str:
                if value is None:
                    return None
                return str(value).strip()
            if isinstance(value, (dict, list)):
                continue
            if isinstance(value, bool):
                return int(value)
            return value
        return None

    @staticmethod
    def _to_int(value: object) -> int | None:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, int):
            return value if 0 < value <= 65535 else None
        if isinstance(value, str):
            value = value.strip()
            if not value.isdigit():
                return None
            port = int(value)
            return port if 0 < port <= 65535 else None
        return None

    @staticmethod
    def _extract_proxy_from_url(raw: str) -> tuple[str, int, str, str] | None:
        candidate = raw.strip()
        if "://" not in candidate:
            candidate = f"http://{candidate}"
        parsed = urllib.parse.urlsplit(candidate)
        if not parsed.hostname or not parsed.port:
            return None
        return parsed.hostname, parsed.port, parsed.username or "", parsed.password or ""

    async def _connect_via_upstream(
        self,
        upstream: _UpstreamProxy,
        host: str,
        port: int,
        headers: dict[str, tuple[str, str]],
        is_connect: bool = False,
    ) -> _Endpoint | None:
        if upstream.transport == "http":
            return await self._connect_http(
                host=host,
                port=port,
                headers=headers,
                proxy_host=upstream.host,
                proxy_port=upstream.port,
                proxy_username=upstream.username,
                proxy_password=upstream.password,
                is_connect=is_connect,
            )
        if upstream.transport == "socks5":
            return await self._connect_socks5(
                host=host,
                port=port,
                proxy_host=upstream.host,
                proxy_port=upstream.port,
                proxy_username=upstream.username,
                proxy_password=upstream.password,
            )
        raise RuntimeError(f"Unsupported proxy transport: {upstream.transport}")

    async def _forward_request_to_origin(
        self,
        method: str,
        request_target: str,
        headers: dict[str, tuple[str, str]],
        host: str,
        port: int,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        upstream: _Endpoint,
        force_absolute: bool = False,
    ) -> None:
        del port
        skip = {
            "proxy-connection",
            "connection",
            "keep-alive",
            "te",
            "trailer",
            "proxy-authorization",
            "proxy-authenticate",
            "upgrade",
            "transfer-encoding",
        }

        origin_request = request_target
        if force_absolute:
            if "://" not in request_target:
                origin_request = f"http://{host}{request_target}"
        else:
            if "://" in request_target:
                parsed = urllib.parse.urlsplit(request_target)
                origin_request = (parsed.path or "/")
                if parsed.query:
                    origin_request = f"{origin_request}?{parsed.query}"
                if not origin_request.startswith("/"):
                    origin_request = f"/{origin_request}"

        request_lines = [f"{method} {origin_request} HTTP/1.1\r\n"]
        for key, (raw_key, value) in headers.items():
            if key.lower() in skip:
                continue
            if key.lower() == "host" and ":" not in raw_key:
                request_lines.append(f"Host: {raw_key}\r\n")
            else:
                request_lines.append(f"{raw_key}: {value}\r\n")
        request_lines.append("Connection: close\r\n")
        request_lines.append("\r\n")
        upstream.writer.write("".join(request_lines).encode("utf-8"))
        await upstream.writer.drain()

        await self._pipe_bidirectional(
            client_reader, client_writer, upstream.reader, upstream.writer
        )

    async def _connect_direct(self, host: str, port: int) -> _Endpoint | None:
        try:
            reader, writer = await asyncio.open_connection(host, port)
            return _Endpoint(reader, writer, host, port)
        except OSError as e:
            LOGGER.warning("direct connect failed %s:%s - %s", host, port, e)
            return None

    async def _connect_http(
        self,
        host: str,
        port: int,
        headers: dict[str, tuple[str, str]],
        proxy_host: str,
        proxy_port: int,
        proxy_username: str = "",
        proxy_password: str = "",
        is_connect: bool = False,
        request_target: str = "",
    ) -> _Endpoint | None:
        del headers
        del request_target
        if not proxy_host or proxy_port <= 0:
            return await self._connect_direct(host, port)

        try:
            reader, writer = await asyncio.open_connection(proxy_host, proxy_port)
        except OSError as e:
            LOGGER.warning(
                "upstream http connect failed %s:%s - %s", proxy_host, proxy_port, e
            )
            return None

        if is_connect:
            connect_line = (
                f"CONNECT {host}:{port} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
            )
            if proxy_username:
                auth = base64.b64encode(
                    f"{proxy_username}:{proxy_password}".encode()
                ).decode()
                connect_line += f"Proxy-Authorization: Basic {auth}\r\n"
            connect_line += "Proxy-Connection: keep-alive\r\n\r\n"
            writer.write(connect_line.encode())
            await writer.drain()

            status = await reader.readline()
            if not status:
                writer.close()
                return None
            status_str = status.decode(errors="ignore")
            if " 200" not in status_str:
                await self._drain_http_headers(reader)
                writer.close()
                return None
            await self._drain_http_headers(reader)
            return _Endpoint(reader, writer, host, port)

        return _Endpoint(reader, writer, host, port)

    async def _connect_socks5(
        self,
        host: str,
        port: int,
        proxy_host: str,
        proxy_port: int,
        proxy_username: str = "",
        proxy_password: str = "",
    ) -> _Endpoint | None:
        if not proxy_host or proxy_port <= 0:
            return await self._connect_direct(host, port)

        try:
            reader, writer = await asyncio.open_connection(proxy_host, proxy_port)
        except OSError as e:
            LOGGER.warning(
                "upstream socks5 connect failed %s:%s - %s", proxy_host, proxy_port, e
            )
            return None

        if proxy_username:
            writer.write(b"\x05\x02\x00\x02")
        else:
            writer.write(b"\x05\x01\x00")
        await writer.drain()

        choice = await reader.readexactly(2)
        if choice[1] == 0x00:
            pass
        elif choice[1] == 0x02:
            username = proxy_username.encode("utf-8")
            password = proxy_password.encode("utf-8")
            if len(username) > 255 or len(password) > 255:
                raise ValueError("socks5 auth credentials too long")
            writer.write(
                b"\x01"
                + bytes([len(username)])
                + username
                + bytes([len(password)])
                + password
            )
            await writer.drain()
            auth_resp = await reader.readexactly(2)
            if auth_resp[1] != 0x00:
                raise RuntimeError("socks5 auth failed")
        else:
            raise RuntimeError("socks5 auth method mismatch")

        host_bytes = host.encode("utf-8")
        if len(host_bytes) > 255:
            raise ValueError("host too long for socks5")

        request = bytearray()
        request.extend(b"\x05\x01\x00\x03")
        request.extend(bytes([len(host_bytes)]))
        request.extend(host_bytes)
        request.extend(port.to_bytes(2, "big"))
        writer.write(request)
        await writer.drain()

        response = await reader.readexactly(4)
        if response[1] != 0x00:
            raise RuntimeError(f"socks5 connect failed code={response[1]}")

        if response[3] == 0x01:
            await reader.readexactly(6)
        elif response[3] == 0x03:
            ln = await reader.readexactly(1)
            await reader.readexactly(ln[0] + 2)
        elif response[3] == 0x04:
            await reader.readexactly(18)
        else:
            raise RuntimeError("socks5 parse failed")
        return _Endpoint(reader, writer, host, port)

    async def _pipe_bidirectional(
        self,
        left_reader: asyncio.StreamReader,
        left_writer: asyncio.StreamWriter,
        right_reader: asyncio.StreamReader,
        right_writer: asyncio.StreamWriter,
    ) -> None:
        async def pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            try:
                while True:
                    data = await src.read(32768)
                    if not data:
                        break
                    dst.write(data)
                    await dst.drain()
            except Exception:
                pass
            finally:
                try:
                    dst.close()
                except Exception:
                    pass

        await asyncio.gather(
            pipe(left_reader, right_writer), pipe(right_reader, left_writer)
        )

        if not right_writer.is_closing():
            right_writer.close()
            await right_writer.wait_closed()
        if not left_writer.is_closing():
            left_writer.close()
            await left_writer.wait_closed()

    async def _drain_http_headers(self, reader: asyncio.StreamReader) -> None:
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break

    async def _read_headers(
        self, reader: asyncio.StreamReader
    ) -> dict[str, tuple[str, str]]:
        raw_headers: dict[str, tuple[str, str]] = {}
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            if b":" not in line:
                continue
            key, value = line.decode("utf-8", errors="ignore").split(":", 1)
            raw_headers[key.strip().lower()] = (key.strip(), value.strip())
        return raw_headers

    async def _error_response(
        self, writer: asyncio.StreamWriter, code: int, message: str
    ) -> None:
        body = f"{code} {message}".encode()
        writer.write(
            (
                f"HTTP/1.1 {code} {message}\r\n"
                "Content-Type: text/plain; charset=utf-8\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("utf-8")
            + body
        )
        await writer.drain()

    @staticmethod
    def _split_host_port(value: str | None, default_port: int) -> tuple[str | None, int]:
        if not value:
            return None, default_port
        host_part = value.strip()
        if host_part.startswith("["):
            if "]" not in host_part:
                return None, default_port
            host, rest = host_part[1:].split("]", 1)
            if rest.startswith(":") and rest[1:].isdigit():
                return host, int(rest[1:])
            return host, default_port
        if ":" in host_part:
            h, p = host_part.rsplit(":", 1)
            if p.isdigit():
                return h, int(p)
        return host_part, default_port

    @staticmethod
    def _clean_host_header(host_header: str) -> str:
        if not host_header:
            return host_header
        return host_header.split(":", 1)[0].strip()
