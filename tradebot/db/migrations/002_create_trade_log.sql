-- 002_create_trade_log.sql
-- Real-time trade event log populated by MT5 EA webhook push.
-- Drives the live Telegram dashboard. Billing uses the authoritative
-- vilona_ledger (cron-reconciled from broker), this table is for UX.

CREATE TABLE IF NOT EXISTS trade_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id         TEXT    NOT NULL,
    platform        TEXT    NOT NULL DEFAULT 'mt5',
    symbol          TEXT    NOT NULL,
    ticket_id       TEXT    NOT NULL UNIQUE,
    magic_number    TEXT    NOT NULL DEFAULT '7771041',
    pnl             REAL    NOT NULL DEFAULT 0.0,
    closed_at       TEXT    NOT NULL,
    processed       INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_trade_log_chat ON trade_log(chat_id);
CREATE INDEX IF NOT EXISTS idx_trade_log_closed ON trade_log(closed_at);
CREATE INDEX IF NOT EXISTS idx_trade_log_processed ON trade_log(processed);
