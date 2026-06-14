"""Tests for tradebot/api/trade_webhook.py — HMAC auth + trade-close webhook."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradebot.config import settings

_TEST_SECRET = "test-secret-change-in-production-32bytes!!"

# Save original settings for restore after webhook tests mutate them
_ORIG_DATA_DIR = settings.DATA_DIR
_ORIG_STORAGE_PATH = settings.STORAGE_DB_PATH


def _setup(secret: str | None = None):
    key = secret if secret is not None else _TEST_SECRET
    os.environ["VILONA_WEBHOOK_SECRET"] = key
    settings.VILONA_WEBHOOK_SECRET = key


def _make_client(db_path: str, secret: str | None = None) -> TestClient:
    from tradebot.api.trade_webhook import router

    _setup(secret)
    settings.DATA_DIR = os.path.dirname(db_path)
    settings.STORAGE_DB_PATH = db_path

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _restore_settings():
    """Restore global settings after any test that mutates them."""
    yield
    settings.DATA_DIR = _ORIG_DATA_DIR
    settings.STORAGE_DB_PATH = _ORIG_STORAGE_PATH


def _payload(**overrides) -> dict:
    defaults = {
        "chat_id": "12345",
        "platform": "mt5",
        "symbol": "XAUUSD",
        "ticket": "987654321",
        "pnl": 50.75,
        "magic": "7771041",
        "closed_at": "2026-06-14T12:00:00Z",
    }
    defaults.update(overrides)
    return defaults


def _sign(body: dict | str) -> str:
    from tradebot.api.webhook_auth import compute_hmac_signature
    if isinstance(body, dict):
        body = json.dumps(body)
    return compute_hmac_signature(body)


# ═══════════════════════════════════════════════════════════════════


class TestWebhookAuth:
    """HMAC signature validation."""

    def test_rejects_missing_header(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        client = _make_client(db_path)
        try:
            resp = client.post(
                "/api/webhook/trade-close",
                content=json.dumps(_payload()),
            )
            assert resp.status_code == 401
        finally:
            os.unlink(db_path)

    def test_rejects_invalid_signature(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        client = _make_client(db_path)
        try:
            resp = client.post(
                "/api/webhook/trade-close",
                content=json.dumps(_payload()),
                headers={"X-Vilona-Signature": "bad"},
            )
            assert resp.status_code == 401
        finally:
            os.unlink(db_path)

    def test_accepts_valid_signature(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        client = _make_client(db_path)
        body = _payload()
        try:
            resp = client.post(
                "/api/webhook/trade-close",
                content=json.dumps(body),
                headers={"X-Vilona-Signature": _sign(body)},
            )
            assert resp.status_code == 200
            assert resp.json()["ok"] is True
        finally:
            os.unlink(db_path)


class TestTradeLogInsert:
    """Trade-close events are persisted to trade_log."""

    def test_inserts_record(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        client = _make_client(db_path)
        body = _payload()
        try:
            resp = client.post(
                "/api/webhook/trade-close",
                content=json.dumps(body),
                headers={"X-Vilona-Signature": _sign(body)},
            )
            assert resp.status_code == 200

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM trade_log WHERE ticket_id = ?",
                (body["ticket"],),
            ).fetchone()
            conn.close()

            assert row is not None
            assert row["chat_id"] == "12345"
            assert row["symbol"] == "XAUUSD"
            assert row["pnl"] == pytest.approx(50.75)
            assert row["processed"] == 0
        finally:
            os.unlink(db_path)

    def test_deduplicates_ticket(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        client = _make_client(db_path)
        body = _payload()
        try:
            client.post("/api/webhook/trade-close", content=json.dumps(body),
                        headers={"X-Vilona-Signature": _sign(body)})
            client.post("/api/webhook/trade-close", content=json.dumps(_payload(pnl=99.99)),
                        headers={"X-Vilona-Signature": _sign(_payload(pnl=99.99))})

            conn = sqlite3.connect(db_path)
            count = conn.execute(
                "SELECT COUNT(*) FROM trade_log WHERE ticket_id = ?",
                (body["ticket"],),
            ).fetchone()[0]
            conn.close()
            assert count == 1
        finally:
            os.unlink(db_path)

    def test_multiple_trades(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        client = _make_client(db_path)
        try:
            for i in range(5):
                body = _payload(ticket=f"T-{i}", pnl=10.0 * (i + 1))
                client.post("/api/webhook/trade-close", content=json.dumps(body),
                            headers={"X-Vilona-Signature": _sign(body)})

            conn = sqlite3.connect(db_path)
            count = conn.execute("SELECT COUNT(*) FROM trade_log").fetchone()[0]
            conn.close()
            assert count == 5
        finally:
            os.unlink(db_path)


class TestPayloadValidation:
    """Invalid payloads return 400."""

    def test_rejects_invalid_json(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        client = _make_client(db_path)
        bad = "{not valid json"
        try:
            resp = client.post(
                "/api/webhook/trade-close",
                content=bad,
                headers={"X-Vilona-Signature": _sign(bad)},
            )
            assert resp.status_code == 400
        finally:
            os.unlink(db_path)

    def test_rejects_missing_required(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        client = _make_client(db_path)
        body = {"chat_id": "1", "symbol": "BTCUSD"}
        try:
            resp = client.post(
                "/api/webhook/trade-close",
                content=json.dumps(body),
                headers={"X-Vilona-Signature": _sign(body)},
            )
            assert resp.status_code == 400
        finally:
            os.unlink(db_path)


class TestGetTradeLog:
    """GET /api/webhook/trade-log."""

    def test_empty_db(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        client = _make_client(db_path)
        try:
            resp = client.get("/api/webhook/trade-log")
            assert resp.json()["trades"] == []
        finally:
            os.unlink(db_path)

    def test_filter_by_chat_id(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        client = _make_client(db_path)
        try:
            for uid, ticket in [("111", "A1"), ("111", "A2"), ("222", "B1")]:
                body = _payload(chat_id=uid, ticket=ticket)
                client.post("/api/webhook/trade-close", content=json.dumps(body),
                            headers={"X-Vilona-Signature": _sign(body)})

            resp = client.get("/api/webhook/trade-log?chat_id=111&limit=10")
            trades = resp.json()["trades"]
            assert len(trades) == 2
            assert all(t["chat_id"] == "111" for t in trades)
        finally:
            os.unlink(db_path)


class TestHMACSignature:
    """compute_hmac_signature utility."""

    def test_consistent(self):
        _setup()
        body = json.dumps(_payload())
        from tradebot.api.webhook_auth import compute_hmac_signature
        assert compute_hmac_signature(body) == compute_hmac_signature(body)

    def test_different(self):
        _setup()
        from tradebot.api.webhook_auth import compute_hmac_signature
        s1 = compute_hmac_signature(json.dumps(_payload(pnl=10.0)))
        s2 = compute_hmac_signature(json.dumps(_payload(pnl=20.0)))
        assert s1 != s2

    def test_hex_format(self):
        _setup()
        from tradebot.api.webhook_auth import compute_hmac_signature
        sig = compute_hmac_signature(json.dumps(_payload()))
        assert len(sig) == 64
        assert all(c in "0123456789abcdef" for c in sig)
