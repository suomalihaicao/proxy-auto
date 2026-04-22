from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any

from dash import Dash, Input, Output, State, callback, dcc, dash_table, html
from dash import callback_context
from dash.exceptions import PreventUpdate
from flask import request as flask_request

from .auth import COOKIE_NAME, parse_user
from .config import load_settings, save_settings
from .db import (
    add_proxy_group,
    add_rule,
    batch_delete_rules,
    batch_move_rules,
    create_user,
    list_proxy_groups,
    list_rules,
    list_users,
    remove_proxy_group,
    update_proxy_group,
    update_user_password,
)


BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "app.db"
SETTINGS_PATH = BASE_DIR / "data" / "settings.json"


RULE_KIND_OPTIONS = [
    {"label": "精确", "value": "exact"},
    {"label": "后缀", "value": "suffix"},
    {"label": "关键词", "value": "keyword"},
]

MODE_OPTIONS = [
    {"label": "直连", "value": "direct"},
    {"label": "单 IP", "value": "single_ip"},
    {"label": "API 获取", "value": "api"},
    {"label": "BigData API", "value": "bigdata_api"},
]

METHOD_OPTIONS = [
    {"label": "GET", "value": "GET"},
    {"label": "POST", "value": "POST"},
    {"label": "PUT", "value": "PUT"},
    {"label": "PATCH", "value": "PATCH"},
]

RULE_KIND_TEXT = {
    "exact": "精确",
    "suffix": "后缀",
    "keyword": "关键词",
}

MODE_TEXT = {
    "direct": "直连",
    "single_ip": "单 IP",
    "api": "API 获取",
    "bigdata_api": "BigData API",
}


def _get_user() -> str:
    return parse_user(flask_request.cookies.get(COOKIE_NAME))


def _require_user() -> str:
    user = _get_user()
    if not user:
        raise PreventUpdate
    return user


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _to_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_proxy_mode(mode: str) -> str:
    normalized = (mode or "").strip().lower()
    if normalized in {"http", "socks5"}:
        return "single_ip"
    if normalized not in {"direct", "single_ip", "api", "bigdata_api"}:
        return "direct"
    return normalized


def _parse_int_default(value: object, default: int, minimum: int = 0, maximum: int | None = None) -> int:
    number = _to_int(value, default=default)
    if number < minimum:
        number = minimum
    if maximum is not None and number > maximum:
        number = maximum
    return number


def _normalize_group_payload(form: dict[str, Any]) -> dict[str, Any]:
    mode = _normalize_proxy_mode(form.get("mode", "direct"))
    protocol = (form.get("protocol", "http") or "http").strip() or "http"
    proxy_host = (form.get("proxy_host") or "").strip()
    proxy_port = _parse_int_default(form.get("proxy_port", 0), 0, 0, 65535)
    pool = (form.get("proxy_pool") or "").strip()
    round_robin = 1 if (form.get("proxy_round_robin") in {"1", "true", "on", "yes", True}) else 0
    api_method = (str(form.get("api_method", "GET")).strip().upper() or "GET")
    if api_method not in {"GET", "POST", "PUT", "PATCH"}:
        api_method = "GET"

    if mode == "direct":
        protocol = "http"
    if mode == "single_ip" and not proxy_host and not pool:
        raise ValueError("single_ip_missing_host")
    if mode == "api" and not (str(form.get("api_url") or "").strip()):
        raise ValueError("api_missing_url")
    if mode == "bigdata_api" and not (str(form.get("bigdata_api_url") or "").strip() or str(form.get("api_url") or "").strip()):
        raise ValueError("bigdata_missing_url")

    return {
        "mode": mode,
        "protocol": protocol,
        "proxy_host": proxy_host,
        "proxy_port": proxy_port,
        "proxy_username": (form.get("proxy_username") or "").strip(),
        "proxy_password": (form.get("proxy_password") or ""),
        "proxy_pool": pool,
        "proxy_round_robin": round_robin,
        "api_url": (form.get("api_url") or "").strip(),
        "api_method": api_method,
        "api_timeout": _parse_int_default(form.get("api_timeout", 8), 8, 1, 120),
        "api_cache_ttl": _parse_int_default(form.get("api_cache_ttl", 20), 20, 0, 86400),
        "api_headers": (form.get("api_headers") or "").strip(),
        "api_body": (form.get("api_body") or ""),
        "api_host_key": (form.get("api_host_key") or "host").strip() or "host",
        "api_port_key": (form.get("api_port_key") or "port").strip() or "port",
        "api_username_key": (form.get("api_username_key") or "username").strip() or "username",
        "api_password_key": (form.get("api_password_key") or "password").strip() or "password",
        "api_proxy_field": (form.get("api_proxy_field") or "proxy").strip() or "proxy",
        "bigdata_api_url": (form.get("bigdata_api_url") or "").strip(),
        "bigdata_api_token": (form.get("bigdata_api_token") or "").strip(),
    }


def _group_options(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    options = [{"label": "全部分组", "value": 0}]
    options.extend({"label": g.get("name", f"分组 {g['id']}"), "value": int(g["id"])} for g in groups)
    return options


def _group_count_map(rules: list[dict[str, Any]]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for rule in rules:
        gid = int(rule.get("group_id", 1) or 1)
        counts[gid] = counts.get(gid, 0) + 1
    return counts


def _field(label: str, component):
    return html.Div(className="p-field", children=[html.Label(label, className="p-label"), component])


def _build_layout():
    if not _get_user():
        return html.Div(
            [
                dcc.Location(id="dash-login-redirect", pathname="/login", refresh=True),
                html.Div("未登录，正在跳转到登录页...", className="muted"),
            ]
        )

    return html.Div(
        className="dash-page",
        children=[
            dcc.Store(id="rules-refresh", data=0),
            dcc.Store(id="proxy-refresh", data=0),
            dcc.Store(id="system-refresh", data=0),
            dcc.Store(id="proxy-store", data={"groups": [], "rules": []}),
            dcc.Store(id="system-store", data={"groups": [], "users": [], "settings": {}}),
            html.Div(
                className="topbar",
                children=[
                    html.Div("域名代理网关管理", className="brand"),
                    html.Div(f"当前用户：{_get_user()}", className="top-info"),
                    html.A("退出登录", href="/logout", className="btn btn-outline"),
                ],
            ),
            dcc.Tabs(
                id="main-tab",
                value="rules",
                children=[
                    dcc.Tab(label="域名配置", value="rules"),
                    dcc.Tab(label="拨号设置", value="proxy"),
                    dcc.Tab(label="系统设置", value="system"),
                ],
            ),
            html.Div(id="panel-rules", className="panel", children=[
                html.Div(className="panel-grid", children=[
                    html.Div(className="panel-aside", children=[
                        html.Div("按分组筛选", className="p-title"),
                        dcc.Dropdown(id="rule-group-filter", options=[{"label": "全部分组", "value": 0}], value=0, clearable=False),
                        html.Div(id="rule-summary", className="muted"),
                    ]),
                    html.Div(className="panel-main", children=[
                        html.Div(className="p-title", children="域名规则管理"),
                        html.Div(className="form-two", children=[
                            _field("域名/关键字", dcc.Input(id="rule-pattern", type="text")),
                            _field("匹配方式", dcc.Dropdown(id="rule-kind", options=RULE_KIND_OPTIONS, value="exact", clearable=False)),
                            _field("所属分组", dcc.Dropdown(id="rule-add-group", options=[{"label": "请先建分组", "value": 0}], value=0, clearable=False)),
                        ]),
                        html.Div(className="btn-row", children=[
                            html.Button("快速精确", id="rule-fill-exact", className="btn btn-outline"),
                            html.Button("快速后缀", id="rule-fill-suffix", className="btn btn-outline"),
                            html.Button("快速关键词", id="rule-fill-keyword", className="btn btn-outline"),
                            html.Button("新增规则", id="rule-add", className="btn btn-primary"),
                        ]),
                        html.Div(className="btn-row", children=[
                            html.Button("批量删除", id="rule-batch-delete", className="btn btn-danger"),
                            dcc.Dropdown(id="rule-move-target", options=[{"label": "请选择目标分组", "value": 0}], value=0, clearable=False, style={"width": "280px"}),
                            html.Button("批量移动", id="rule-batch-move", className="btn btn-warning"),
                        ]),
                        dash_table.DataTable(
                            id="rule-table",
                            columns=[
                                {"name": "编号", "id": "id"},
                                {"name": "类型", "id": "kind"},
                                {"name": "规则", "id": "pattern"},
                                {"name": "分组", "id": "group_name"},
                            ],
                            data=[],
                            row_id="id",
                            row_selectable="multi",
                            page_size=20,
                            style_cell={"padding": "8px", "fontSize": "13px"},
                        ),
                    ]),
                ]),
            ]),
            html.Div(id="panel-proxy", className="panel", style={"display": "none"}, children=[
                html.Div(className="panel-grid", children=[
                    html.Div(className="panel-aside", children=[
                        html.Div("分组列表", className="p-title"),
                        html.Div(id="proxy-group-card-list"),
                    ]),
                    html.Div(className="panel-main", children=[
                        html.Div(className="p-title", children="新增代理分组"),
                        _field("分组名称", dcc.Input(id="proxy-add-name", type="text")),
                        _field("模式", dcc.Dropdown(id="proxy-add-mode", options=MODE_OPTIONS, value="direct", clearable=False)),
                        _field("上游协议", dcc.Dropdown(id="proxy-add-protocol", options=[{"label":"HTTP","value":"http"},{"label":"SOCKS5","value":"socks5"}], value="http", clearable=False)),
                        html.Div(id="proxy-add-direct-note", className="muted", children="直连模式将不使用任何上游。"),
                        html.Div(id="proxy-add-single", className="sub-panel", children=[
                            _field("单节点地址", dcc.Input(id="proxy-add-proxy-host", type="text")),
                            _field("端口", dcc.Input(id="proxy-add-proxy-port", type="number")),
                            _field("账号", dcc.Input(id="proxy-add-proxy-user", type="text")),
                            _field("密码", dcc.Input(id="proxy-add-proxy-pass", type="text")),
                            _field("地址池（ip:端口:用户:密钥）", dcc.Textarea(id="proxy-add-proxy-pool", rows=3)),
                            _field("每次请求轮换 IP", dcc.Checklist(id="proxy-add-proxy-round", options=[{"label":"开启","value":"1"}], value=[])),
                        ]),
                        html.Div(id="proxy-add-api", className="sub-panel", children=[
                            _field("API 地址", dcc.Input(id="proxy-add-api-url", type="text")),
                            _field("请求方法", dcc.Dropdown(id="proxy-add-api-method", options=METHOD_OPTIONS, value="GET", clearable=False)),
                            _field("超时(秒)", dcc.Input(id="proxy-add-api-timeout", type="number")),
                            _field("TTL(秒)", dcc.Input(id="proxy-add-api-cache-ttl", type="number")),
                            _field("请求头JSON", dcc.Textarea(id="proxy-add-api-headers", rows=2)),
                            _field("请求体JSON", dcc.Textarea(id="proxy-add-api-body", rows=2)),
                            _field("主机字段", dcc.Input(id="proxy-add-api-host-key", type="text", value="host")),
                            _field("端口字段", dcc.Input(id="proxy-add-api-port-key", type="text", value="port")),
                            _field("用户名字段", dcc.Input(id="proxy-add-api-username-key", type="text", value="username")),
                            _field("密码字段", dcc.Input(id="proxy-add-api-password-key", type="text", value="password")),
                            _field("完整字段", dcc.Input(id="proxy-add-api-proxy-field", type="text", value="proxy")),
                        ]),
                        html.Div(id="proxy-add-bigdata", className="sub-panel", children=[
                            _field("BigData 地址", dcc.Input(id="proxy-add-bd-url", type="text")),
                            _field("BigData Token", dcc.Input(id="proxy-add-bd-token", type="text")),
                        ]),
                        html.Button("新增分组", id="proxy-add-btn", className="btn btn-primary"),

                        html.Div(className="mt-4 p-title", children="编辑代理分组"),
                        _field("选择分组", dcc.Dropdown(id="proxy-edit-id", options=[], value=0, clearable=False)),
                        _field("分组名称", dcc.Input(id="proxy-edit-name", type="text")),
                        _field("模式", dcc.Dropdown(id="proxy-edit-mode", options=MODE_OPTIONS, value="direct", clearable=False)),
                        _field("上游协议", dcc.Dropdown(id="proxy-edit-protocol", options=[{"label":"HTTP","value":"http"},{"label":"SOCKS5","value":"socks5"}], value="http", clearable=False)),
                        html.Div(id="proxy-edit-direct-note", className="muted", children="直连模式将不使用任何上游。"),
                        html.Div(id="proxy-edit-single", className="sub-panel", children=[
                            _field("单节点地址", dcc.Input(id="proxy-edit-proxy-host", type="text")),
                            _field("端口", dcc.Input(id="proxy-edit-proxy-port", type="number")),
                            _field("账号", dcc.Input(id="proxy-edit-proxy-user", type="text")),
                            _field("密码", dcc.Input(id="proxy-edit-proxy-pass", type="text")),
                            _field("地址池（ip:端口:用户:密钥）", dcc.Textarea(id="proxy-edit-proxy-pool", rows=3)),
                            _field("每次请求轮换 IP", dcc.Checklist(id="proxy-edit-proxy-round", options=[{"label":"开启","value":"1"}], value=[])),
                        ]),
                        html.Div(id="proxy-edit-api", className="sub-panel", children=[
                            _field("API 地址", dcc.Input(id="proxy-edit-api-url", type="text")),
                            _field("请求方法", dcc.Dropdown(id="proxy-edit-api-method", options=METHOD_OPTIONS, value="GET", clearable=False)),
                            _field("超时(秒)", dcc.Input(id="proxy-edit-api-timeout", type="number")),
                            _field("TTL(秒)", dcc.Input(id="proxy-edit-api-cache-ttl", type="number")),
                            _field("请求头JSON", dcc.Textarea(id="proxy-edit-api-headers", rows=2)),
                            _field("请求体JSON", dcc.Textarea(id="proxy-edit-api-body", rows=2)),
                            _field("主机字段", dcc.Input(id="proxy-edit-api-host-key", type="text")),
                            _field("端口字段", dcc.Input(id="proxy-edit-api-port-key", type="text")),
                            _field("用户名字段", dcc.Input(id="proxy-edit-api-username-key", type="text")),
                            _field("密码字段", dcc.Input(id="proxy-edit-api-password-key", type="text")),
                            _field("完整字段", dcc.Input(id="proxy-edit-api-proxy-field", type="text")),
                        ]),
                        html.Div(id="proxy-edit-bigdata", className="sub-panel", children=[
                            _field("BigData 地址", dcc.Input(id="proxy-edit-bd-url", type="text")),
                            _field("BigData Token", dcc.Input(id="proxy-edit-bd-token", type="text")),
                        ]),
                        html.Div(className="btn-row", children=[
                            html.Button("保存分组", id="proxy-save-btn", className="btn btn-primary"),
                            html.Button("删除分组", id="proxy-delete-btn", className="btn btn-danger"),
                        ]),
                    ]),
                ]),
            ]),
            html.Div(id="panel-system", className="panel", style={"display": "none"}, children=[
                html.Div(className="panel-grid", children=[
                    html.Div(className="panel-aside", children=[
                        html.Div(className="p-title", children="系统参数"),
                        _field("代理监听地址", dcc.Input(id="system-listen-host", type="text")),
                        _field("代理监听端口", dcc.Input(id="system-listen-port", type="number")),
                        _field("Web 监听地址", dcc.Input(id="system-web-host", type="text")),
                        _field("Web 监听端口", dcc.Input(id="system-web-port", type="number")),
                        _field("默认分组", dcc.Dropdown(id="system-default-group", options=[], value=0, clearable=False)),
                        _field("白名单IP（空表示全部）", dcc.Input(id="system-allow-ips", type="text")),
                        html.Button("保存系统设置", id="system-save-btn", className="btn btn-primary"),
                    ]),
                    html.Div(className="panel-main", children=[
                        html.Div(className="p-title", children="用户管理"),
                        _field("新增用户", dcc.Input(id="system-new-user", type="text")),
                        _field("初始化密码", dcc.Input(id="system-new-password", type="text")),
                        html.Button("新增用户", id="system-add-user", className="btn btn-primary"),
                        html.Div(className="mt-4", children="重置密码"),
                        _field("选择用户", dcc.Dropdown(id="system-user-id", options=[], value=0, clearable=False)),
                        _field("新密码", dcc.Input(id="system-reset-password", type="text")),
                        html.Button("重置密码", id="system-reset-btn", className="btn btn-warning"),
                        html.Div("用户列表", className="p-title mt-4"),
                        dash_table.DataTable(
                            id="system-user-table",
                            columns=[{"name":"ID","id":"id"},{"name":"用户名","id":"username"},{"name":"创建时间","id":"created_at"}],
                            data=[],
                            page_size=8,
                            style_cell={"padding":"6px", "fontSize":"12px"},
                        ),
                    ]),
                ]),
            ]),
        ],
    )


def _get_common_payload() -> dict[str, Any]:
    return {
        "rules": [_row_to_dict(r) for r in list_rules(DB_PATH)],
        "groups": [_row_to_dict(g) for g in list_proxy_groups(DB_PATH)],
        "users": [_row_to_dict(u) for u in list_users(DB_PATH)],
        "settings": load_settings(SETTINGS_PATH).as_dict(),
    }


def _toggle_mode_blocks(mode: str) -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, str]]:
    m = _normalize_proxy_mode(mode)
    if m == "direct":
        return {"display": "none"}, {"display": "none"}, {"display": "none"}, {"display": "none"}
    if m == "api":
        return {"display": "none"}, {"display": "none"}, {"display": "block"}, {"display": "none"}
    if m == "bigdata_api":
        return {"display": "none"}, {"display": "none"}, {"display": "none"}, {"display": "block"}
    return {"display": "block"}, {"display": "block"}, {"display": "none"}, {"display": "none"}


@callback(Output("panel-rules", "style"), Output("panel-proxy", "style"), Output("panel-system", "style"), Input("main-tab", "value"))
def _switch_tab(value: str):
    _require_user()
    if value == "proxy":
        return {"display": "none"}, {"display": "block"}, {"display": "none"}
    if value == "system":
        return {"display": "none"}, {"display": "none"}, {"display": "block"}
    return {"display": "block"}, {"display": "none"}, {"display": "none"}


@callback(
    Output("rule-table", "data"),
    Output("rule-group-filter", "options"),
    Output("rule-group-filter", "value"),
    Output("rule-add-group", "options"),
    Output("rule-move-target", "options"),
    Output("rule-summary", "children"),
    Input("rules-refresh", "data"),
    State("rule-group-filter", "value"),
)
def _refresh_rule_panel(_, current_filter):
    _require_user()
    payload = _get_common_payload()
    rules = payload["rules"]
    groups = payload["groups"]
    count_map = _group_count_map(rules)
    all_options = _group_options(groups)

    selected = _to_int(current_filter, 0)
    if selected not in {0} and not any(int(g["id"]) == selected for g in groups):
        selected = 0

    if selected > 0:
        show_rules = [r for r in rules if int(r.get("group_id", 0) or 0) == selected]
        selected_text = next((g["name"] for g in groups if int(g["id"]) == selected), "当前分组")
    else:
        show_rules = rules
        selected_text = "全部分组"

    for item in show_rules:
        item["kind"] = RULE_KIND_TEXT.get(str(item.get("kind", "")), item.get("kind", ""))
        if item.get("proxy_mode"):
            item["proxy_mode_text"] = MODE_TEXT.get(str(item.get("proxy_mode", "")), str(item.get("proxy_mode", "")))
        item["group_name"] = item.get("group_name") or f"分组 {item.get('group_id', '-')}"

    summary = f"{selected_text}，共 {len(show_rules)} 条，分组总数 {len(groups)}"
    options_for_add = [o for o in all_options if o["value"] != 0] or [{"label": "请先建分组", "value": 0}]

    return show_rules, all_options, selected, options_for_add, all_options, summary


@callback(
    Output("rules-refresh", "data"),
    Input("rule-add", "n_clicks"),
    Input("rule-batch-delete", "n_clicks"),
    Input("rule-batch-move", "n_clicks"),
    State("rule-pattern", "value"),
    State("rule-kind", "value"),
    State("rule-add-group", "value"),
    State("rule-table", "selected_row_ids"),
    State("rule-move-target", "value"),
    State("rules-refresh", "data"),
    prevent_initial_call=True,
)
def _rule_ops(add_click, del_click, move_click, pattern, kind, target_gid, selected_rows, move_gid, current_state):
    _require_user()
    current = _to_int(current_state, 0)
    triggered = callback_context.triggered
    if not triggered:
        raise PreventUpdate

    trigger = triggered[0]["prop_id"].split(".")[0]

    if trigger == "rule-add":
        pattern = (pattern or "").strip()
        if not pattern or kind not in {"exact", "suffix", "keyword"}:
            return current
        gid = _to_int(target_gid, 0)
        groups = {_to_int(g["id"]) for g in [_row_to_dict(g) for g in list_proxy_groups(DB_PATH)]}
        if gid <= 0 or gid not in groups:
            return current
        add_rule(DB_PATH, pattern, kind, gid)
        return current + 1

    ids = [_to_int(i) for i in (selected_rows or []) if _to_int(i) > 0]
    if not ids:
        return current
    if trigger == "rule-batch-delete":
        batch_delete_rules(DB_PATH, ids)
        return current + 1

    if trigger == "rule-batch-move":
        tid = _to_int(move_gid, 0)
        groups = {_to_int(g["id"]) for g in [_row_to_dict(g) for g in list_proxy_groups(DB_PATH)]}
        if tid <= 0 or tid not in groups:
            return current
        batch_move_rules(DB_PATH, ids, tid)
        return current + 1
    return current


@callback(Output("rule-pattern", "value"), Output("rule-kind", "value"), Input("rule-fill-exact", "n_clicks"), Input("rule-fill-suffix", "n_clicks"), Input("rule-fill-keyword", "n_clicks"), prevent_initial_call=True)
def _quick_fill_rules(_, __, ___):
    _require_user()
    triggered = callback_context.triggered
    if not triggered:
        raise PreventUpdate

    trigger = triggered[0]["prop_id"].split(".")[0]
    if trigger == "rule-fill-exact":
        return "api.example.com", "exact"
    if trigger == "rule-fill-suffix":
        return "*.example.com", "suffix"
    if trigger == "rule-fill-keyword":
        return "paypal", "keyword"
    return "", "exact"


@callback(
    Output("proxy-store", "data"),
    Input("proxy-refresh", "data"),
)
def _refresh_proxy_store(_):
    _require_user()
    payload = _get_common_payload()
    return payload


@callback(Output("proxy-group-card-list", "children"), Output("proxy-edit-id", "options"), Output("rule-add-group", "options"), Output("rule-move-target", "options"), Input("proxy-store", "data"))
def _render_proxy_summary(payload):
    _require_user()
    groups = payload.get("groups", [])
    rules = payload.get("rules", [])
    count_map = _group_count_map(rules)
    cards = []
    if not groups:
        cards.append(html.Div("暂无分组，先在本页创建分组。", className="muted"))
    for g in groups:
        gid = _to_int(g["id"], 0)
        cards.append(
            html.Div(
                className="card-item",
                children=[
                    html.Div(f"{g.get('name', '未命名')}（ID: {gid}）"),
                    html.Div(f"模式：{g.get('proxy_mode', '-')}", className="muted"),
                    html.Div(f"已绑定规则：{count_map.get(gid,0)} 条"),
                ],
            )
        )
    options = _group_options(groups)
    add_options = [o for o in options if o["value"] != 0]
    if not add_options:
        add_options = [{"label": "请先建分组", "value": 0}]
    return cards, options, add_options, options


@callback(
    Output("proxy-add-single", "style"),
    Output("proxy-edit-single", "style"),
    Output("proxy-add-api", "style"),
    Output("proxy-edit-api", "style"),
    Output("proxy-add-bigdata", "style"),
    Output("proxy-edit-bigdata", "style"),
    Input("proxy-add-mode", "value"),
    Input("proxy-edit-mode", "value"),
)
def _toggle_proxy_mode(add_mode, edit_mode):
    _require_user()
    add_modes = _toggle_mode_blocks(add_mode)
    edit_modes = _toggle_mode_blocks(edit_mode)
    # direct + api + bigdata; each expects [single, api, bigdata]
    return (
        add_modes[0],  # add single
        edit_modes[0], # edit single
        add_modes[2],  # add api
        edit_modes[2], # edit api
        add_modes[3],  # add bigdata
        edit_modes[3], # edit bigdata
    )


@callback(
    Output("proxy-edit-name", "value"),
    Output("proxy-edit-mode", "value"),
    Output("proxy-edit-protocol", "value"),
    Output("proxy-edit-proxy-host", "value"),
    Output("proxy-edit-proxy-port", "value"),
    Output("proxy-edit-proxy-user", "value"),
    Output("proxy-edit-proxy-pass", "value"),
    Output("proxy-edit-proxy-pool", "value"),
    Output("proxy-edit-proxy-round", "value"),
    Output("proxy-edit-api-url", "value"),
    Output("proxy-edit-api-method", "value"),
    Output("proxy-edit-api-timeout", "value"),
    Output("proxy-edit-api-cache-ttl", "value"),
    Output("proxy-edit-api-headers", "value"),
    Output("proxy-edit-api-body", "value"),
    Output("proxy-edit-api-host-key", "value"),
    Output("proxy-edit-api-port-key", "value"),
    Output("proxy-edit-api-username-key", "value"),
    Output("proxy-edit-api-password-key", "value"),
    Output("proxy-edit-api-proxy-field", "value"),
    Output("proxy-edit-bd-url", "value"),
    Output("proxy-edit-bd-token", "value"),
    Input("proxy-edit-id", "value"),
    Input("proxy-store", "data"),
)
def _fill_proxy_edit(selected_id, payload):
    _require_user()
    groups = payload.get("groups", [])
    gid = _to_int(selected_id, 0)
    target = None
    for g in groups:
        if _to_int(g["id"]) == gid:
            target = g
            break
    if target is None:
        return (
            "", "direct", "http", "", 0, "", "", "", [],
            "", "GET", 8, 20, "", "", "host", "port", "username", "password", "proxy", "", "",
        )
    return (
        target.get("name", ""),
        target.get("proxy_mode", "direct"),
        target.get("proxy_protocol", "http"),
        target.get("proxy_host", ""),
        _to_int(target.get("proxy_port", 0)),
        target.get("proxy_username", ""),
        target.get("proxy_password", ""),
        target.get("proxy_pool", ""),
        ["1"] if int(target.get("proxy_round_robin", 0) or 0) else [],
        target.get("api_url", ""),
        target.get("api_method", "GET"),
        _to_int(target.get("api_timeout", 8), 8),
        _to_int(target.get("api_cache_ttl", 20), 20),
        target.get("api_headers", ""),
        target.get("api_body", ""),
        target.get("api_host_key", "host"),
        target.get("api_port_key", "port"),
        target.get("api_username_key", "username"),
        target.get("api_password_key", "password"),
        target.get("api_proxy_field", "proxy"),
        target.get("bigdata_api_url", ""),
        target.get("bigdata_api_token", ""),
    )


@callback(
    Output("proxy-refresh", "data"),
    Input("proxy-add-btn", "n_clicks"),
    Input("proxy-save-btn", "n_clicks"),
    Input("proxy-delete-btn", "n_clicks"),
    State("proxy-add-name", "value"),
    State("proxy-add-mode", "value"),
    State("proxy-add-protocol", "value"),
    State("proxy-add-proxy-host", "value"),
    State("proxy-add-proxy-port", "value"),
    State("proxy-add-proxy-user", "value"),
    State("proxy-add-proxy-pass", "value"),
    State("proxy-add-proxy-pool", "value"),
    State("proxy-add-proxy-round", "value"),
    State("proxy-add-api-url", "value"),
    State("proxy-add-api-method", "value"),
    State("proxy-add-api-timeout", "value"),
    State("proxy-add-api-cache-ttl", "value"),
    State("proxy-add-api-headers", "value"),
    State("proxy-add-api-body", "value"),
    State("proxy-add-api-host-key", "value"),
    State("proxy-add-api-port-key", "value"),
    State("proxy-add-api-username-key", "value"),
    State("proxy-add-api-password-key", "value"),
    State("proxy-add-api-proxy-field", "value"),
    State("proxy-add-bd-url", "value"),
    State("proxy-add-bd-token", "value"),
    State("proxy-edit-id", "value"),
    State("proxy-edit-name", "value"),
    State("proxy-edit-mode", "value"),
    State("proxy-edit-protocol", "value"),
    State("proxy-edit-proxy-host", "value"),
    State("proxy-edit-proxy-port", "value"),
    State("proxy-edit-proxy-user", "value"),
    State("proxy-edit-proxy-pass", "value"),
    State("proxy-edit-proxy-pool", "value"),
    State("proxy-edit-proxy-round", "value"),
    State("proxy-edit-api-url", "value"),
    State("proxy-edit-api-method", "value"),
    State("proxy-edit-api-timeout", "value"),
    State("proxy-edit-api-cache-ttl", "value"),
    State("proxy-edit-api-headers", "value"),
    State("proxy-edit-api-body", "value"),
    State("proxy-edit-api-host-key", "value"),
    State("proxy-edit-api-port-key", "value"),
    State("proxy-edit-api-username-key", "value"),
    State("proxy-edit-api-password-key", "value"),
    State("proxy-edit-api-proxy-field", "value"),
    State("proxy-edit-bd-url", "value"),
    State("proxy-edit-bd-token", "value"),
    State("proxy-refresh", "data"),
    prevent_initial_call=True,
)
def _proxy_ops(
    add_click, save_click, del_click, add_name, add_mode, add_protocol,
    add_host, add_port, add_user, add_passwd, add_pool, add_round,
    add_api_url, add_api_method, add_api_timeout, add_api_ttl, add_api_headers, add_api_body,
    add_api_host_key, add_api_port_key, add_api_user_key, add_api_pass_key, add_api_field_key,
    add_bd_url, add_bd_token, edit_id, edit_name, edit_mode, edit_protocol, edit_host, edit_port,
    edit_user, edit_passwd, edit_pool, edit_round, edit_api_url, edit_api_method, edit_api_timeout,
    edit_api_ttl, edit_api_headers, edit_api_body, edit_api_host_key, edit_api_port_key, edit_api_user_key,
    edit_api_pass_key, edit_api_field_key, edit_bd_url, edit_bd_token, current_state,
):
    _require_user()
    triggered = callback_context.triggered
    if not triggered:
        raise PreventUpdate

    trigger = triggered[0]["prop_id"].split(".")[0]
    current = _to_int(current_state, 0)

    if trigger == "proxy-add-btn":
        name = (add_name or "").strip()
        if not name:
            return current
        try:
            payload = _normalize_group_payload(
                {
                    "mode": add_mode,
                    "protocol": add_protocol,
                    "proxy_host": add_host,
                    "proxy_port": add_port,
                    "proxy_username": add_user,
                    "proxy_password": add_passwd,
                    "proxy_pool": add_pool,
                    "proxy_round_robin": "1" if "1" in (add_round or []) else "0",
                    "api_url": add_api_url,
                    "api_method": add_api_method,
                    "api_timeout": add_api_timeout,
                    "api_cache_ttl": add_api_ttl,
                    "api_headers": add_api_headers,
                    "api_body": add_api_body,
                    "api_host_key": add_api_host_key,
                    "api_port_key": add_api_port_key,
                    "api_username_key": add_api_user_key,
                    "api_password_key": add_api_pass_key,
                    "api_proxy_field": add_api_field_key,
                    "bigdata_api_url": add_bd_url,
                    "bigdata_api_token": add_bd_token,
                }
            )
        except ValueError:
            return current
        try:
            add_proxy_group(
                DB_PATH,
                name=name,
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
            return current
        return current + 1

    if trigger == "proxy-save-btn":
        gid = _to_int(edit_id, 0)
        groups = {_to_int(g["id"]) for g in [_row_to_dict(g) for g in list_proxy_groups(DB_PATH)]}
        if gid <= 0 or gid not in groups:
            return current
        try:
            payload = _normalize_group_payload(
                {
                    "mode": edit_mode,
                    "protocol": edit_protocol,
                    "proxy_host": edit_host,
                    "proxy_port": edit_port,
                    "proxy_username": edit_user,
                    "proxy_password": edit_passwd,
                    "proxy_pool": edit_pool,
                    "proxy_round_robin": "1" if "1" in (edit_round or []) else "0",
                    "api_url": edit_api_url,
                    "api_method": edit_api_method,
                    "api_timeout": edit_api_timeout,
                    "api_cache_ttl": edit_api_ttl,
                    "api_headers": edit_api_headers,
                    "api_body": edit_api_body,
                    "api_host_key": edit_api_host_key,
                    "api_port_key": edit_api_port_key,
                    "api_username_key": edit_api_user_key,
                    "api_password_key": edit_api_pass_key,
                    "api_proxy_field": edit_api_field_key,
                    "bigdata_api_url": edit_bd_url,
                    "bigdata_api_token": edit_bd_token,
                }
            )
        except ValueError:
            return current
        name = (edit_name or "").strip()
        if not name:
            return current
        try:
            updated = update_proxy_group(
                DB_PATH,
                group_id=gid,
                name=name,
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
            return current

        if updated:
            return current + 1
        return current

    if trigger == "proxy-delete-btn":
        gid = _to_int(edit_id, 0)
        if gid <= 0:
            return current
        if remove_proxy_group(DB_PATH, gid):
            return current + 1
        return current
    return current


@callback(
    Output("system-store", "data"),
    Input("system-refresh", "data"),
)
def _refresh_system_store(_):
    _require_user()
    return _get_common_payload()


@callback(
    Output("system-listen-host", "value"),
    Output("system-listen-port", "value"),
    Output("system-web-host", "value"),
    Output("system-web-port", "value"),
    Output("system-default-group", "options"),
    Output("system-default-group", "value"),
    Output("system-allow-ips", "value"),
    Output("system-user-id", "options"),
    Output("system-user-table", "data"),
    Input("system-store", "data"),
)
def _load_system_panel(data):
    _require_user()
    settings = (data or {}).get("settings", {})
    groups = (data or {}).get("groups", [])
    users = (data or {}).get("users", [])
    g_options = _group_options(groups)
    g_default = _to_int(settings.get("default_proxy_group_id", 0), 0)
    if g_default <= 0 and groups:
        g_default = int(groups[0]["id"])
    u_options = [{"label": u.get("username", ""), "value": int(u["id"])} for u in users] or [{"label": "暂无用户", "value": 0}]
    return (
        settings.get("listen_host", "0.0.0.0"),
        _to_int(settings.get("listen_port", 3128), 3128),
        settings.get("web_host", "0.0.0.0"),
        _to_int(settings.get("web_port", 8080), 8080),
        g_options,
        g_default,
        settings.get("allowed_client_ips", ""),
        u_options,
        users,
    )


@callback(
    Output("system-refresh", "data"),
    Input("system-save-btn", "n_clicks"),
    Input("system-add-user", "n_clicks"),
    Input("system-reset-btn", "n_clicks"),
    State("system-listen-host", "value"),
    State("system-listen-port", "value"),
    State("system-web-host", "value"),
    State("system-web-port", "value"),
    State("system-default-group", "value"),
    State("system-allow-ips", "value"),
    State("system-new-user", "value"),
    State("system-new-password", "value"),
    State("system-user-id", "value"),
    State("system-reset-password", "value"),
    State("system-refresh", "data"),
    prevent_initial_call=True,
)
def _system_ops(
    save_btn, add_btn, reset_btn,
    listen_host, listen_port, web_host, web_port, default_gid, allow_ips,
    new_user, new_password, user_id, reset_password, current_state
):
    _require_user()
    current = _to_int(current_state, 0)
    triggered = callback_context.triggered
    if not triggered:
        raise PreventUpdate

    trigger = triggered[0]["prop_id"].split(".")[0]

    if trigger == "system-save-btn":
        settings = load_settings(SETTINGS_PATH)
        settings.listen_host = (listen_host or "0.0.0.0").strip() or "0.0.0.0"
        settings.listen_port = _to_int(listen_port, 3128)
        settings.web_host = (web_host or "0.0.0.0").strip() or "0.0.0.0"
        settings.web_port = _to_int(web_port, 8080)
        settings.allowed_client_ips = (allow_ips or "").strip()
        gid = _to_int(default_gid, 0)
        groups = {_to_int(g["id"]) for g in [_row_to_dict(g) for g in list_proxy_groups(DB_PATH)]}
        if gid <= 0 or gid not in groups:
            gid = min(groups) if groups else 1
        settings.default_proxy_group_id = gid
        save_settings(SETTINGS_PATH, settings)
        return current + 1

    if trigger == "system-add-user":
        username = (new_user or "").strip()
        password = (new_password or "").strip()
        if username and password:
            try:
                create_user(DB_PATH, username, password)
            except Exception:
                return current
            return current + 1
        return current

    if trigger == "system-reset-btn":
        uid = _to_int(user_id, 0)
        pwd = (reset_password or "").strip()
        if uid > 0 and pwd and update_user_password(DB_PATH, uid, pwd):
            return current + 1
        return current
    return current


def create_dash_app() -> Dash:
    app = Dash(
        __name__,
        requests_pathname_prefix="/ui/",
        routes_pathname_prefix="/ui/",
        suppress_callback_exceptions=True,
        assets_folder=str(BASE_DIR / "app" / "assets"),
    )
    app.title = "域名代理网关管理"
    app.layout = _build_layout
    return app
