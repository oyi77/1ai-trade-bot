-- 001_create_ledger.sql
-- Gotong Royong Performance Fee Ledger
-- High-Water Mark accounting for weekly profit-sharing billing.

CREATE TABLE IF NOT EXISTS vilona_ledger (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id         TEXT    NOT NULL,
    platform        TEXT    NOT NULL DEFAULT 'ccxt',
    period_start    TEXT    NOT NULL,
    period_end      TEXT    NOT NULL,
    hwm_baseline    REAL    NOT NULL DEFAULT 0.0,
    bot_pnl         REAL    NOT NULL DEFAULT 0.0,
    hwm_new         REAL    NOT NULL DEFAULT 0.0,
    fee_amount      REAL    NOT NULL DEFAULT 0.0,
    payment_status  TEXT    NOT NULL DEFAULT 'unpaid'
                    CHECK(payment_status IN ('unpaid', 'paid', 'waived')),
    generated_at    TEXT    NOT NULL,
    paid_at         TEXT    DEFAULT NULL,

    UNIQUE(chat_id, platform, period_start)
);

CREATE INDEX IF NOT EXISTS idx_ledger_chat ON vilona_ledger(chat_id, platform);
CREATE INDEX IF NOT EXISTS idx_ledger_status ON vilona_ledger(payment_status);
