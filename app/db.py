from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    iterations = 150000
    key = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations, dklen=32
    )
    return f"pbkdf2_sha256${iterations}${salt}${base64.b64encode(key).decode()}"


def _verify_password(password: str, hashed: str) -> bool:
    try:
        algorithm, iter_s, salt, digest = hashed.split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    iterations = int(iter_s)
    key = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations, dklen=32
    )
    target = base64.b64decode(digest)
    return hmac.compare_digest(key, target)


def get_conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _schema_sql_path() -> Path:
    return Path(__file__).resolve().parent / "schema.sql"


def _read_schema_sql() -> str:
    return _schema_sql_path().read_text(encoding="utf-8")


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_read_schema_sql())


def _utc_now() -> str:
    return datetime.utcnow().isoformat()


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)


def _normalize_proxy_mode(mode: str) -> str:
    value = (mode or "").strip().lower()
    if value in {"http", "socks5"}:
        return "single_ip"
    if value not in {"direct", "single_ip", "api", "bigdata_api"}:
        return "direct"
    return value


def ensure_default_proxy_group(
    db_path: Path,
    *,
    mode: str = "direct",
    proxy_protocol: str = "http",
    proxy_host: str = "",
    proxy_port: int = 0,
    proxy_username: str = "",
    proxy_password: str = "",
    proxy_pool: str = "",
    proxy_round_robin: int = 0,
    api_url: str = "",
    api_method: str = "GET",
    api_timeout: int = 8,
    api_cache_ttl: int = 20,
    api_headers: str = "",
    api_body: str = "",
    api_host_key: str = "host",
    api_port_key: str = "port",
    api_username_key: str = "username",
    api_password_key: str = "password",
    api_proxy_field: str = "proxy",
    bigdata_api_url: str = "",
    bigdata_api_token: str = "",
) -> int:
    mode = _normalize_proxy_mode(mode)
    if mode == "direct":
        proxy_protocol = "http"
    with get_conn(db_path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS proxy_groups (id INTEGER PRIMARY KEY AUTOINCREMENT)")
        row = conn.execute("SELECT id FROM proxy_groups ORDER BY id ASC LIMIT 1").fetchone()
        if row is not None:
            return int(row["id"])

        now = _utc_now()
        cur = conn.execute(
            """
            INSERT INTO proxy_groups (
                name,
                proxy_mode,
                proxy_protocol,
                proxy_host,
                proxy_port,
                proxy_username,
                proxy_password,
                api_url,
                api_method,
                api_timeout,
                api_cache_ttl,
                api_headers,
                api_body,
                api_host_key,
                api_port_key,
                api_username_key,
                api_password_key,
                api_proxy_field,
                proxy_pool,
                proxy_round_robin,
                bigdata_api_url,
                bigdata_api_token,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "默认代理组",
                mode,
                (proxy_protocol or "http").strip() or "http",
                (proxy_host or "").strip(),
                int(proxy_port),
                (proxy_username or "").strip(),
                (proxy_password or "").strip(),
                (api_url or "").strip(),
                (api_method or "GET").upper(),
                int(api_timeout),
                int(api_cache_ttl),
                (api_headers or ""),
                (api_body or ""),
                (api_host_key or "host").strip(),
                (api_port_key or "port").strip(),
                (api_username_key or "username").strip(),
                (api_password_key or "password").strip(),
                (api_proxy_field or "proxy").strip(),
                (proxy_pool or "").strip(),
                1 if int(proxy_round_robin) > 0 else 0,
                (bigdata_api_url or "").strip(),
                (bigdata_api_token or "").strip(),
                now,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def init_db(db_path: Path) -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    def _migrate(conn: sqlite3.Connection) -> None:
        if not all(_has_table(conn, table) for table in ("users", "rules", "settings", "proxy_groups")):
            _ensure_schema(conn)

        if not _has_column(conn, "proxy_groups", "proxy_pool"):
            conn.execute(
                "ALTER TABLE proxy_groups ADD COLUMN proxy_pool TEXT NOT NULL DEFAULT ''"
            )
        if not _has_column(conn, "proxy_groups", "proxy_round_robin"):
            conn.execute(
                "ALTER TABLE proxy_groups ADD COLUMN proxy_round_robin INTEGER NOT NULL DEFAULT 0"
            )
        if not _has_column(conn, "rules", "group_id"):
            conn.execute(
                "ALTER TABLE rules ADD COLUMN group_id INTEGER NOT NULL DEFAULT 1"
            )
        if _has_column(conn, "proxy_groups", "proxy_pool"):
            conn.execute(
                "UPDATE proxy_groups SET proxy_pool = '' WHERE proxy_pool IS NULL"
            )
        if _has_column(conn, "proxy_groups", "proxy_round_robin"):
            conn.execute(
                "UPDATE proxy_groups SET proxy_round_robin = 0 WHERE proxy_round_robin IS NULL"
            )
        if _has_column(conn, "rules", "group_id"):
            conn.execute(
                "UPDATE rules SET group_id = 1 WHERE group_id IS NULL OR group_id <= 0"
            )
        conn.commit()

    try:
        with get_conn(db_path) as conn:
            _migrate(conn)
    except sqlite3.DatabaseError:
        backup_path = db_path.with_name(f"{db_path.name}.invalid")
        if db_path.exists():
            try:
                db_path.replace(backup_path)
            except OSError:
                db_path.unlink()
        with get_conn(db_path) as conn:
            _migrate(conn)


def create_user(db_path: Path, username: str, password: str) -> None:
    pw = _hash_password(password)
    now = _utc_now()
    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, pw, now),
        )
        conn.commit()


def get_user(db_path: Path, username: str) -> Optional[sqlite3.Row]:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? LIMIT 1",
            (username,),
        ).fetchone()
        return row


def verify_user(db_path: Path, username: str, password: str) -> bool:
    row = get_user(db_path, username)
    if not row:
        return False
    return _verify_password(password, row["password_hash"])


def list_users(db_path: Path) -> list[sqlite3.Row]:
    with get_conn(db_path) as conn:
        return conn.execute("SELECT * FROM users ORDER BY id ASC").fetchall()


def update_user_password(db_path: Path, user_id: int, password: str) -> bool:
    pw = _hash_password(password)
    with get_conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (pw, user_id),
        )
        conn.commit()
        return cur.rowcount > 0


def user_exists(db_path: Path, username: str) -> bool:
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT 1 FROM users WHERE username = ? LIMIT 1", (username,)).fetchone()
        return row is not None


def list_rules(db_path: Path) -> list[sqlite3.Row]:
    with get_conn(db_path) as conn:
        return conn.execute(
            """
            SELECT
                r.id,
                r.pattern,
                r.kind,
                COALESCE(r.group_id, 1) AS group_id,
                pg.name AS group_name
            FROM rules r
            LEFT JOIN proxy_groups pg ON pg.id = r.group_id
            ORDER BY r.id DESC
            """
        ).fetchall()


def add_rule(db_path: Path, pattern: str, kind: str, group_id: int) -> int:
    now = _utc_now()
    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO rules (pattern, kind, group_id, enabled, created_at) VALUES (?, ?, ?, 1, ?)",
            (pattern.strip(), kind, group_id, now),
        )
        rowid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        return int(rowid)


def batch_move_rules(db_path: Path, rule_ids: list[int], target_group_id: int) -> int:
    ids = sorted({int(r) for r in rule_ids if int(r) > 0})
    if not ids:
        return 0
    placeholders = ", ".join(["?"] * len(ids))
    with get_conn(db_path) as conn:
        cur = conn.execute(
            f"UPDATE rules SET group_id = ? WHERE id IN ({placeholders})",
            (int(target_group_id), *ids),
        )
        conn.commit()
        return int(cur.rowcount or 0)


def batch_delete_rules(db_path: Path, rule_ids: list[int]) -> int:
    ids = sorted({int(r) for r in rule_ids if int(r) > 0})
    if not ids:
        return 0
    placeholders = ", ".join(["?"] * len(ids))
    with get_conn(db_path) as conn:
        cur = conn.execute(f"DELETE FROM rules WHERE id IN ({placeholders})", ids)
        conn.commit()
        return int(cur.rowcount or 0)


def remove_rule(db_path: Path, rule_id: int) -> bool:
    with get_conn(db_path) as conn:
        cur = conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
        conn.commit()
        return cur.rowcount > 0


def get_setting(db_path: Path, key: str, default: str = "") -> str:
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else default


def set_setting(db_path: Path, key: str, value: str) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        conn.commit()


def get_all_rules_for_matcher(db_path: Path) -> list[dict]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                pattern,
                kind,
                COALESCE(group_id, 1) AS group_id
            FROM rules
            WHERE enabled = 1
            ORDER BY id ASC
            """
        ).fetchall()
        return [
            {"pattern": r["pattern"], "kind": r["kind"], "group_id": int(r["group_id"])}
            for r in rows
        ]


def create_default_proxy_group(
    db_path: Path,
    name: str = "默认代理组",
    proxy_mode: str = "direct",
    proxy_protocol: str = "http",
) -> int:
    mode = _normalize_proxy_mode(proxy_mode)
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM proxy_groups WHERE name = ? LIMIT 1",
            (name,),
        ).fetchone()
        if row:
            return int(row["id"])
        cur = conn.execute(
            """
            INSERT INTO proxy_groups (
                name,
                proxy_mode,
                proxy_protocol,
                created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (name, mode, (proxy_protocol or "http").strip() or "http", _utc_now()),
        )
        rowid = cur.lastrowid
        conn.commit()
        return int(rowid)


def list_proxy_groups(db_path: Path) -> list[sqlite3.Row]:
    with get_conn(db_path) as conn:
        return conn.execute(
            """
            SELECT * FROM proxy_groups WHERE enabled = 1 ORDER BY id ASC
            """
        ).fetchall()


def add_proxy_group(
    db_path: Path,
    name: str,
    proxy_mode: str,
    proxy_protocol: str = "http",
    proxy_host: str = "",
    proxy_port: int = 0,
    proxy_username: str = "",
    proxy_password: str = "",
    proxy_pool: str = "",
    proxy_round_robin: int = 0,
    api_url: str = "",
    api_method: str = "GET",
    api_timeout: int = 8,
    api_cache_ttl: int = 20,
    api_headers: str = "",
    api_body: str = "",
    api_host_key: str = "host",
    api_port_key: str = "port",
    api_username_key: str = "username",
    api_password_key: str = "password",
    api_proxy_field: str = "proxy",
    bigdata_api_url: str = "",
    bigdata_api_token: str = "",
) -> int:
    now = _utc_now()
    mode = _normalize_proxy_mode(proxy_mode)
    with get_conn(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO proxy_groups (
                name,
                proxy_mode,
                proxy_protocol,
                proxy_host,
                proxy_port,
                proxy_username,
                proxy_password,
                api_url,
                api_method,
                api_timeout,
                api_cache_ttl,
                api_headers,
                api_body,
                api_host_key,
                api_port_key,
                api_username_key,
                api_password_key,
                api_proxy_field,
                proxy_pool,
                proxy_round_robin,
                bigdata_api_url,
                bigdata_api_token,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name.strip(),
                mode,
                (proxy_protocol or "http").strip() or "http",
                (proxy_host or "").strip(),
                int(proxy_port),
                (proxy_username or "").strip(),
                proxy_password or "",
                (api_url or "").strip(),
                (api_method or "GET").upper(),
                int(api_timeout),
                int(api_cache_ttl),
                (api_headers or ""),
                api_body or "",
                (api_host_key or "host").strip(),
                (api_port_key or "port").strip(),
                (api_username_key or "username").strip(),
                (api_password_key or "password").strip(),
                (api_proxy_field or "proxy").strip(),
                (proxy_pool or "").strip(),
                1 if int(proxy_round_robin) > 0 else 0,
                (bigdata_api_url or "").strip(),
                (bigdata_api_token or "").strip(),
                now,
            ),
        )
        rowid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        return int(rowid)


def get_proxy_group(db_path: Path, group_id: int) -> Optional[sqlite3.Row]:
    with get_conn(db_path) as conn:
        return conn.execute(
            "SELECT * FROM proxy_groups WHERE id = ? LIMIT 1",
            (group_id,),
        ).fetchone()


def get_proxy_group_ids(db_path: Path) -> set[int]:
    with get_conn(db_path) as conn:
        rows = conn.execute("SELECT id FROM proxy_groups WHERE enabled = 1 ORDER BY id ASC").fetchall()
        return {int(r["id"]) for r in rows}


def remove_proxy_group(db_path: Path, group_id: int) -> bool:
    with get_conn(db_path) as conn:
        in_use = conn.execute(
            "SELECT 1 FROM rules WHERE group_id = ? LIMIT 1",
            (group_id,),
        ).fetchone()
        if in_use is not None:
            return False
        cur = conn.execute("DELETE FROM proxy_groups WHERE id = ?", (group_id,))
        conn.commit()
        return cur.rowcount > 0


def update_proxy_group(
    db_path: Path,
    group_id: int,
    *,
    name: str,
    proxy_mode: str,
    proxy_protocol: str = "http",
    proxy_host: str = "",
    proxy_port: int = 0,
    proxy_username: str = "",
    proxy_password: str = "",
    proxy_pool: str = "",
    proxy_round_robin: int = 0,
    api_url: str = "",
    api_method: str = "GET",
    api_timeout: int = 8,
    api_cache_ttl: int = 20,
    api_headers: str = "",
    api_body: str = "",
    api_host_key: str = "host",
    api_port_key: str = "port",
    api_username_key: str = "username",
    api_password_key: str = "password",
    api_proxy_field: str = "proxy",
    bigdata_api_url: str = "",
    bigdata_api_token: str = "",
) -> bool:
    mode = _normalize_proxy_mode(proxy_mode)
    with get_conn(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE proxy_groups SET
                name = ?,
                proxy_mode = ?,
                proxy_protocol = ?,
                proxy_host = ?,
                proxy_port = ?,
                proxy_username = ?,
                proxy_password = ?,
                api_url = ?,
                api_method = ?,
                api_timeout = ?,
                api_cache_ttl = ?,
                api_headers = ?,
                api_body = ?,
                api_host_key = ?,
                api_port_key = ?,
                api_username_key = ?,
                api_password_key = ?,
                api_proxy_field = ?,
                proxy_pool = ?,
                proxy_round_robin = ?,
                bigdata_api_url = ?,
                bigdata_api_token = ?
            WHERE id = ?
            """,
            (
                name.strip(),
                mode,
                (proxy_protocol or "http").strip() or "http",
                (proxy_host or "").strip(),
                int(proxy_port),
                (proxy_username or "").strip(),
                proxy_password or "",
                (api_url or "").strip(),
                (api_method or "GET").upper(),
                int(api_timeout),
                int(api_cache_ttl),
                (api_headers or ""),
                api_body or "",
                (api_host_key or "host").strip(),
                (api_port_key or "port").strip(),
                (api_username_key or "username").strip(),
                (api_password_key or "password").strip(),
                (api_proxy_field or "proxy").strip(),
                (proxy_pool or "").strip(),
                1 if int(proxy_round_robin) > 0 else 0,
                (bigdata_api_url or "").strip(),
                (bigdata_api_token or "").strip(),
                group_id,
            ),
        )
        conn.commit()
        return cur.rowcount > 0


def load_settings_from_db(db_path: Path) -> dict:
    with get_conn(db_path) as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}


def upsert_settings_from_dict(db_path: Path, data: dict) -> None:
    with get_conn(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            ((k, str(v)) for k, v in data.items()),
        )
        conn.commit()
