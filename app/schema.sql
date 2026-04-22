CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('exact', 'suffix', 'keyword')),
    group_id INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS proxy_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    proxy_mode TEXT NOT NULL CHECK (proxy_mode IN ('direct', 'single_ip', 'api', 'bigdata_api', 'http', 'socks5')),
    proxy_protocol TEXT NOT NULL DEFAULT 'http',
    proxy_host TEXT NOT NULL DEFAULT '',
    proxy_port INTEGER NOT NULL DEFAULT 0,
    proxy_username TEXT NOT NULL DEFAULT '',
    proxy_password TEXT NOT NULL DEFAULT '',
    api_url TEXT NOT NULL DEFAULT '',
    api_method TEXT NOT NULL DEFAULT 'GET',
    api_timeout INTEGER NOT NULL DEFAULT 8,
    api_cache_ttl INTEGER NOT NULL DEFAULT 20,
    api_headers TEXT NOT NULL DEFAULT '',
    api_body TEXT NOT NULL DEFAULT '',
    api_host_key TEXT NOT NULL DEFAULT 'host',
    api_port_key TEXT NOT NULL DEFAULT 'port',
    api_username_key TEXT NOT NULL DEFAULT 'username',
    api_password_key TEXT NOT NULL DEFAULT 'password',
    api_proxy_field TEXT NOT NULL DEFAULT 'proxy',
    proxy_pool TEXT NOT NULL DEFAULT '',
    proxy_round_robin INTEGER NOT NULL DEFAULT 0,
    bigdata_api_url TEXT NOT NULL DEFAULT '',
    bigdata_api_token TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
