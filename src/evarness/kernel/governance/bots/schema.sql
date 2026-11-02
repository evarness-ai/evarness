CREATE TABLE IF NOT EXISTS bot_identity (
    bot_id            TEXT PRIMARY KEY,
    name              TEXT NOT NULL UNIQUE,
    status            TEXT NOT NULL CHECK (status IN ('active', 'disabled', 'revoked')),
    workspace_path    TEXT NOT NULL,
    vault_namespace   TEXT NOT NULL UNIQUE,
    memory_scope      TEXT NOT NULL UNIQUE,
    definition_sha256 TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    revoked_at        TEXT
);
