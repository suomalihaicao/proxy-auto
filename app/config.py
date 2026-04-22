from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class ProxySettings:
    listen_host: str = "0.0.0.0"
    listen_port: int = 3128
    web_host: str = "0.0.0.0"
    web_port: int = 8080
    proxy_mode: str = "single_ip"
    proxy_protocol: str = "http"
    proxy_host: str = ""
    proxy_port: int = 0
    proxy_username: str = ""
    proxy_password: str = ""
    api_url: str = ""
    api_method: str = "GET"
    api_timeout: int = 8
    api_cache_ttl: int = 20
    api_headers: str = ""
    api_body: str = ""
    api_host_key: str = "host"
    api_port_key: str = "port"
    api_username_key: str = "username"
    api_password_key: str = "password"
    api_proxy_field: str = "proxy"
    bigdata_api_url: str = ""
    bigdata_api_token: str = ""
    session_secret: str = "change-me"
    allowed_client_ips: str = ""
    default_proxy_group_id: int = 1

    def as_dict(self) -> dict:
        return asdict(self)


def load_settings(path: Path) -> ProxySettings:
    if not path.exists():
        return ProxySettings()
    with path.open("r", encoding="utf-8-sig") as fp:
        raw = json.load(fp)
    return ProxySettings(**{**asdict(ProxySettings()), **raw})


def save_settings(path: Path, settings: ProxySettings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        json.dump(settings.as_dict(), fp, indent=2, ensure_ascii=False)
