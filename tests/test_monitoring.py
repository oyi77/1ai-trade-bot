"""Tests for monitoring modules — metrics, health, and trade tracker."""

from __future__ import annotations

import io
import json
import threading
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tradebot.monitoring.health import HealthHandler, HealthProbe
from tradebot.monitoring.metrics import MetricsCollector, MetricSnapshot
from tradebot.monitoring.tracker import TradeRecord, TradeStats, TradeTracker

# ═══════════════════════════════════════════════════════════════════
#  1. MetricsCollector & MetricSnapshot
# ═══════════════════════════════════════════════════════════════════


class TestMetricSnapshot:
    """MetricSnapshot dataclass creation and serialization."""

    def test_default_creation(self):
        snap = MetricSnapshot()
        assert snap.timestamp == 0.0
        assert snap.signals_total == 0
        assert snap.trades_total == 0
        assert snap.win_rate == 0.0
        assert snap.current_streak == ""
        assert snap.current_streak_count == 0
        assert snap.total_pnl == 0.0
        assert snap.avg_latency_ms == 0.0
        assert snap.max_latency_ms == 0.0
        assert snap.latency_count == 0
        assert snap.errors_total == 0

    def test_creation_with_values(self):
        snap = MetricSnapshot(
            timestamp=1700000000.0,
            signals_total=42,
            signals_by_source={"deriv": 30, "ai": 12},
            signals_by_symbol={"R_75": 40, "R_100": 2},
            trades_total=20,
            trades_won=14,
            trades_lost=6,
            trades_breakeven=0,
            win_rate=0.7,
            current_streak="win",
            current_streak_count=3,
            total_pnl=25.5,
            avg_latency_ms=12.34,
            max_latency_ms=98.76,
            latency_count=500,
            latency_buckets={"<50ms": 450, "<100ms": 40, ">=1000ms": 10},
            engine_votes={"fvg": 15, "sweep": 12},
            errors_total=2,
        )
        assert snap.signals_total == 42
        assert snap.signals_by_source["deriv"] == 30
        assert snap.trades_won == 14
        assert snap.win_rate == 0.7
        assert snap.current_streak == "win"
        assert snap.current_streak_count == 3
        assert snap.engine_votes["fvg"] == 15

    def test_to_dict_structure(self):
        snap = MetricSnapshot(
            signals_total=10,
            signals_by_source={"deriv": 10},
            signals_by_symbol={"R_75": 10},
            trades_total=5,
            trades_won=3,
            trades_lost=2,
            win_rate=0.6,
            current_streak="win",
            current_streak_count=2,
            total_pnl=10.0,
            avg_latency_ms=5.0,
            max_latency_ms=15.0,
            latency_count=100,
            latency_buckets={"<50ms": 90},
            engine_votes={"fvg": 5},
            errors_total=1,
        )
        d = snap.to_dict()
        assert "signals" in d
        assert "trades" in d
        assert "latency" in d
        assert "engine_votes" in d
        assert "errors" in d
        assert d["signals"]["total"] == 10
        assert d["signals"]["by_source"]["deriv"] == 10
        assert d["trades"]["total"] == 5
        assert d["trades"]["won"] == 3
        assert d["trades"]["win_rate"] == 0.6
        assert d["trades"]["streak"] == "win_2"
        assert d["latency"]["avg_ms"] == 5.0
        assert d["latency"]["max_ms"] == 15.0
        assert d["latency"]["count"] == 100
        assert d["errors"] == 1

    def test_to_dict_returns_copies(self):
        """Mutating to_dict result should not affect the snapshot."""
        snap = MetricSnapshot(signals_by_source={"a": 1})
        d = snap.to_dict()
        d["signals"]["by_source"]["a"] = 999
        assert snap.signals_by_source["a"] == 1


class TestMetricsCollectorRecordSignal:
    """MetricsCollector.record_signal() tests."""

    def test_record_single_signal(self):
        mc = MetricsCollector()
        mc.record_signal(source="deriv", symbol="R_75")
        snap = mc.snapshot()
        assert snap.signals_total == 1
        assert snap.signals_by_source == {"deriv": 1}
        assert snap.signals_by_symbol == {"R_75": 1}

    def test_record_multiple_signals_aggregated(self):
        mc = MetricsCollector()
        mc.record_signal(source="deriv", symbol="R_75")
        mc.record_signal(source="deriv", symbol="R_75")
        mc.record_signal(source="ai", symbol="R_100")
        snap = mc.snapshot()
        assert snap.signals_total == 3
        assert snap.signals_by_source == {"deriv": 2, "ai": 1}
        assert snap.signals_by_symbol == {"R_75": 2, "R_100": 1}

    def test_record_signal_empty_source(self):
        mc = MetricsCollector()
        mc.record_signal(source="", symbol="")
        snap = mc.snapshot()
        assert snap.signals_total == 1
        assert snap.signals_by_source == {}
        assert snap.signals_by_symbol == {}


class TestMetricsCollectorRecordTrade:
    """MetricsCollector.record_trade() tests."""

    def test_record_winning_trade(self):
        mc = MetricsCollector()
        mc.record_trade(won=True, pnl=2.5)
        snap = mc.snapshot()
        assert snap.trades_total == 1
        assert snap.trades_won == 1
        assert snap.trades_lost == 0
        assert snap.total_pnl == 2.5
        assert snap.current_streak == "win"
        assert snap.current_streak_count == 1

    def test_record_losing_trade(self):
        mc = MetricsCollector()
        mc.record_trade(won=False, pnl=-1.0)
        snap = mc.snapshot()
        assert snap.trades_total == 1
        assert snap.trades_won == 0
        assert snap.trades_lost == 1
        assert snap.total_pnl == -1.0
        assert snap.current_streak == "loss"
        assert snap.current_streak_count == 1

    def test_record_breakeven_trade(self):
        mc = MetricsCollector()
        mc.record_trade(won=True, pnl=0.0, breakeven=True)
        snap = mc.snapshot()
        assert snap.trades_total == 1
        assert snap.trades_breakeven == 1
        assert snap.trades_won == 0
        assert snap.trades_lost == 0

    def test_win_streak_tracking(self):
        mc = MetricsCollector()
        mc.record_trade(won=True, pnl=1.0)
        mc.record_trade(won=True, pnl=1.5)
        mc.record_trade(won=True, pnl=0.5)
        snap = mc.snapshot()
        assert snap.current_streak == "win"
        assert snap.current_streak_count == 3
        assert snap.trades_won == 3

    def test_loss_streak_tracking(self):
        mc = MetricsCollector()
        mc.record_trade(won=False, pnl=-1.0)
        mc.record_trade(won=False, pnl=-2.0)
        snap = mc.snapshot()
        assert snap.current_streak == "loss"
        assert snap.current_streak_count == 2

    def test_streak_resets_on_outcome_change(self):
        mc = MetricsCollector()
        mc.record_trade(won=True, pnl=1.0)
        mc.record_trade(won=True, pnl=1.0)
        mc.record_trade(won=False, pnl=-1.0)
        snap = mc.snapshot()
        assert snap.current_streak == "loss"
        assert snap.current_streak_count == 1
        assert snap.trades_won == 2
        assert snap.trades_lost == 1

    def test_win_rate_calculation(self):
        mc = MetricsCollector()
        mc.record_trade(won=True, pnl=1.0)
        mc.record_trade(won=True, pnl=1.0)
        mc.record_trade(won=False, pnl=-1.0)
        mc.record_trade(won=False, pnl=-1.0)
        snap = mc.snapshot()
        assert snap.win_rate == 0.5

    def test_win_rate_with_breakeven(self):
        """Breakeven trades should not affect win rate denominator."""
        mc = MetricsCollector()
        mc.record_trade(won=True, pnl=1.0)
        mc.record_trade(won=False, pnl=-1.0)
        mc.record_trade(won=True, pnl=0.0, breakeven=True)
        snap = mc.snapshot()
        # wins=1, losses=1, breakeven=1 → rate = 1/2
        assert snap.win_rate == 0.5

    def test_pnl_accumulation(self):
        mc = MetricsCollector()
        mc.record_trade(won=True, pnl=3.25)
        mc.record_trade(won=False, pnl=-1.10)
        mc.record_trade(won=True, pnl=0.85)
        snap = mc.snapshot()
        assert snap.total_pnl == pytest.approx(3.0, abs=0.01)


class TestMetricsCollectorRecordEngineVote:
    """MetricsCollector.record_engine_vote() tests."""

    def test_record_single_engine(self):
        mc = MetricsCollector()
        mc.record_engine_vote("fvg")
        snap = mc.snapshot()
        assert snap.engine_votes == {"fvg": 1}

    def test_record_multiple_engines(self):
        mc = MetricsCollector()
        mc.record_engine_vote("fvg")
        mc.record_engine_vote("sweep")
        mc.record_engine_vote("fvg")
        mc.record_engine_vote("liquidity")
        mc.record_engine_vote("sweep")
        snap = mc.snapshot()
        assert snap.engine_votes == {"fvg": 2, "sweep": 2, "liquidity": 1}


class TestMetricsCollectorRecordLatency:
    """MetricsCollector.record_latency() with histogram buckets."""

    def test_single_latency(self):
        mc = MetricsCollector()
        mc.record_latency(25.0)
        snap = mc.snapshot()
        assert snap.latency_count == 1
        assert snap.avg_latency_ms == 25.0
        assert snap.max_latency_ms == 25.0

    def test_latency_avg_and_max(self):
        mc = MetricsCollector()
        mc.record_latency(10.0)
        mc.record_latency(30.0)
        mc.record_latency(20.0)
        snap = mc.snapshot()
        assert snap.latency_count == 3
        assert snap.avg_latency_ms == 20.0
        assert snap.max_latency_ms == 30.0

    def test_latency_bucket_lt50(self):
        mc = MetricsCollector()
        mc.record_latency(10.0)
        snap = mc.snapshot()
        assert snap.latency_buckets.get("<50ms") == 1

    def test_latency_bucket_lt100(self):
        mc = MetricsCollector()
        mc.record_latency(75.0)
        snap = mc.snapshot()
        assert snap.latency_buckets.get("<100ms") == 1

    def test_latency_bucket_lt200(self):
        mc = MetricsCollector()
        mc.record_latency(150.0)
        snap = mc.snapshot()
        assert snap.latency_buckets.get("<200ms") == 1

    def test_latency_bucket_lt500(self):
        mc = MetricsCollector()
        mc.record_latency(300.0)
        snap = mc.snapshot()
        assert snap.latency_buckets.get("<500ms") == 1

    def test_latency_bucket_lt1000(self):
        mc = MetricsCollector()
        mc.record_latency(700.0)
        snap = mc.snapshot()
        assert snap.latency_buckets.get("<1000ms") == 1

    def test_latency_bucket_ge_1000(self):
        mc = MetricsCollector()
        mc.record_latency(1500.0)
        snap = mc.snapshot()
        assert snap.latency_buckets.get(">=1000ms") == 1

    def test_latency_bucket_boundary_at_threshold(self):
        """Value exactly at threshold falls into the next bucket."""
        mc = MetricsCollector()
        mc.record_latency(50.0)  # not <50, so <100
        snap = mc.snapshot()
        assert snap.latency_buckets.get("<100ms") == 1
        assert snap.latency_buckets.get("<50ms") is None

    def test_latency_multiple_buckets(self):
        mc = MetricsCollector()
        mc.record_latency(10.0)   # <50ms
        mc.record_latency(80.0)   # <100ms
        mc.record_latency(80.0)   # <100ms
        mc.record_latency(250.0)  # <500ms
        mc.record_latency(1200.0) # >=1000ms
        snap = mc.snapshot()
        assert snap.latency_buckets["<50ms"] == 1
        assert snap.latency_buckets["<100ms"] == 2
        assert snap.latency_buckets["<500ms"] == 1
        assert snap.latency_buckets[">=1000ms"] == 1


class TestMetricsCollectorRecordError:
    """MetricsCollector.record_error() tests."""

    def test_record_errors(self):
        mc = MetricsCollector()
        mc.record_error()
        mc.record_error()
        snap = mc.snapshot()
        assert snap.errors_total == 2


class TestMetricsCollectorSnapshot:
    """MetricsCollector.snapshot() aggregation tests."""

    def test_snapshot_empty_collector(self):
        mc = MetricsCollector()
        snap = mc.snapshot()
        assert snap.signals_total == 0
        assert snap.trades_total == 0
        assert snap.win_rate == 0.0
        assert snap.total_pnl == 0.0
        assert snap.avg_latency_ms == 0.0
        assert snap.errors_total == 0

    def test_snapshot_is_isolated(self):
        """Taking a snapshot should not affect subsequent recordings."""
        mc = MetricsCollector()
        mc.record_signal(source="a", symbol="A")
        snap1 = mc.snapshot()
        mc.record_signal(source="b", symbol="B")
        snap2 = mc.snapshot()
        assert snap1.signals_total == 1
        assert snap2.signals_total == 2

    def test_snapshot_timestamp_set(self):
        mc = MetricsCollector()
        before = time.time()
        snap = mc.snapshot()
        after = time.time()
        assert before <= snap.timestamp <= after

    def test_snapshot_win_rate_no_trades(self):
        mc = MetricsCollector()
        snap = mc.snapshot()
        assert snap.win_rate == 0.0

    def test_snapshot_win_rate_only_wins(self):
        mc = MetricsCollector()
        mc.record_trade(won=True, pnl=1.0)
        mc.record_trade(won=True, pnl=1.0)
        snap = mc.snapshot()
        assert snap.win_rate == 1.0

    def test_snapshot_win_rate_only_losses(self):
        mc = MetricsCollector()
        mc.record_trade(won=False, pnl=-1.0)
        snap = mc.snapshot()
        assert snap.win_rate == 0.0


class TestMetricsCollectorReset:
    """MetricsCollector.reset() clears all data."""

    def test_reset_clears_everything(self):
        mc = MetricsCollector()
        mc.record_signal(source="a", symbol="A")
        mc.record_trade(won=True, pnl=5.0)
        mc.record_latency(42.0)
        mc.record_engine_vote("fvg")
        mc.record_error()

        mc.reset()
        snap = mc.snapshot()
        assert snap.signals_total == 0
        assert snap.signals_by_source == {}
        assert snap.signals_by_symbol == {}
        assert snap.trades_total == 0
        assert snap.trades_won == 0
        assert snap.trades_lost == 0
        assert snap.trades_breakeven == 0
        assert snap.total_pnl == 0.0
        assert snap.current_streak == "none"
        assert snap.current_streak_count == 0
        assert snap.latency_count == 0
        assert snap.avg_latency_ms == 0.0
        assert snap.max_latency_ms == 0.0
        assert snap.latency_buckets == {}
        assert snap.engine_votes == {}
        assert snap.errors_total == 0

    def test_reset_allows_fresh_recording(self):
        mc = MetricsCollector()
        mc.record_trade(won=True, pnl=1.0)
        mc.reset()
        mc.record_trade(won=False, pnl=-2.0)
        snap = mc.snapshot()
        assert snap.trades_total == 1
        assert snap.trades_lost == 1
        assert snap.current_streak == "loss"


class TestMetricsCollectorToPrometheus:
    """MetricsCollector.to_prometheus() text format output."""

    def test_prometheus_contains_standard_lines(self):
        mc = MetricsCollector()
        mc.record_signal(source="deriv", symbol="R_75")
        mc.record_trade(won=True, pnl=1.0)
        mc.record_latency(25.0)
        mc.record_engine_vote("fvg")
        mc.record_error()

        output = mc.to_prometheus()
        assert "tradebot_signals_total 1" in output
        assert "tradebot_trades_total 1" in output
        assert "tradebot_trades_won_total 1" in output
        assert "tradebot_trades_lost_total 0" in output
        assert "tradebot_win_rate" in output
        assert "tradebot_total_pnl" in output
        assert "tradebot_latency_avg_ms" in output
        assert "tradebot_latency_max_ms" in output
        assert "tradebot_latency_count" in output
        assert "tradebot_errors_total 1" in output

    def test_prometheus_has_help_and_type(self):
        mc = MetricsCollector()
        output = mc.to_prometheus()
        assert "# HELP tradebot_signals_total" in output
        assert "# TYPE tradebot_signals_total counter" in output
        assert "# HELP tradebot_trades_total" in output
        assert "# TYPE tradebot_trades_total counter" in output
        assert "# HELP tradebot_win_rate" in output
        assert "# TYPE tradebot_win_rate gauge" in output

    def test_prometheus_latency_buckets(self):
        mc = MetricsCollector()
        mc.record_latency(10.0)
        mc.record_latency(75.0)
        output = mc.to_prometheus()
        assert 'tradebot_latency_bucket{le="50"}' in output
        assert 'tradebot_latency_bucket{le="100"}' in output

    def test_prometheus_engine_votes(self):
        mc = MetricsCollector()
        mc.record_engine_vote("fvg")
        mc.record_engine_vote("sweep")
        output = mc.to_prometheus()
        assert 'tradebot_engine_votes{engine="fvg"} 1' in output
        assert 'tradebot_engine_votes{engine="sweep"} 1' in output

    def test_prometheus_empty_collector(self):
        mc = MetricsCollector()
        output = mc.to_prometheus()
        assert "tradebot_signals_total 0" in output
        assert "tradebot_trades_total 0" in output
        assert "tradebot_errors_total 0" in output


class TestMetricsCollectorThreadSafety:
    """Thread-safety — concurrent recording must not lose data."""

    def test_concurrent_signal_recording(self):
        mc = MetricsCollector()
        n_threads = 8
        signals_per_thread = 100

        def record_many():
            for _ in range(signals_per_thread):
                mc.record_signal(source="test", symbol="R_75")

        threads = [threading.Thread(target=record_many) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        snap = mc.snapshot()
        assert snap.signals_total == n_threads * signals_per_thread

    def test_concurrent_trade_recording(self):
        mc = MetricsCollector()
        n_threads = 4
        trades_per_thread = 50

        def record_wins():
            for _ in range(trades_per_thread):
                mc.record_trade(won=True, pnl=1.0)

        def record_losses():
            for _ in range(trades_per_thread):
                mc.record_trade(won=False, pnl=-1.0)

        threads = []
        for _ in range(n_threads):
            threads.append(threading.Thread(target=record_wins))
            threads.append(threading.Thread(target=record_losses))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        snap = mc.snapshot()
        assert snap.trades_total == n_threads * trades_per_thread * 2
        assert snap.trades_won == n_threads * trades_per_thread
        assert snap.trades_lost == n_threads * trades_per_thread

    def test_concurrent_latency_recording(self):
        mc = MetricsCollector()
        n_threads = 4
        per_thread = 100

        def record_lats():
            for i in range(per_thread):
                mc.record_latency(float(i))

        threads = [threading.Thread(target=record_lats) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        snap = mc.snapshot()
        assert snap.latency_count == n_threads * per_thread

    def test_concurrent_mixed_operations(self):
        """All recording operations concurrently should not crash or lose counts."""
        mc = MetricsCollector()
        n = 50

        def do_signals():
            for _ in range(n):
                mc.record_signal(source="x", symbol="X")

        def do_trades():
            for _ in range(n):
                mc.record_trade(won=True, pnl=1.0)

        def do_latency():
            for _ in range(n):
                mc.record_latency(10.0)

        def do_votes():
            for _ in range(n):
                mc.record_engine_vote("eng")

        def do_errors():
            for _ in range(n):
                mc.record_error()

        threads = [
            threading.Thread(target=fn)
            for fn in [do_signals, do_trades, do_latency, do_votes, do_errors]
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        snap = mc.snapshot()
        assert snap.signals_total == n
        assert snap.trades_total == n
        assert snap.latency_count == n
        assert snap.engine_votes["eng"] == n
        assert snap.errors_total == n


# ═══════════════════════════════════════════════════════════════════
#  2. HealthProbe & HealthHandler
# ═══════════════════════════════════════════════════════════════════


def _make_handler_for_test(method: str = "GET", path: str = "/healthz") -> HealthHandler:
    """Create a HealthHandler wired to fake I/O for a single request."""
    handler = HealthHandler.__new__(HealthHandler)
    handler.rfile = io.BytesIO(b"")
    handler.wfile = io.BytesIO()
    handler.request_version = "HTTP/1.1"
    handler.client_address = ("127.0.0.1", 12345)
    handler.server = MagicMock()
    handler.connection = MagicMock()
    handler.headers = {}
    handler.log_request = lambda *a, **kw: None
    handler.log_error = lambda *a, **kw: None
    handler.close_connection = True
    handler.path = path
    handler._status_code = None
    handler._headers: list[tuple[str, str]] = []
    handler.send_response = lambda code: setattr(handler, "_status_code", code)
    handler.send_header = lambda k, v: handler._headers.append((k, v))
    handler.end_headers = lambda: None
    # Copy class-level extra_checks to instance to avoid Python's descriptor
    # protocol wrapping functions as bound methods (which adds `self` as arg).
    handler.extra_checks = HealthHandler.extra_checks
    return handler


class TestHealthProbeInit:
    """HealthProbe initialization tests."""

    def test_default_init(self):
        with patch("tradebot.monitoring.health.settings") as mock_settings:
            mock_settings.MONITORING_PROMETHEUS_PORT = 8000
            probe = HealthProbe()
            assert probe.host == "127.0.0.1"
            assert probe.port == 8000
            assert probe.running is False

    def test_custom_host_port(self):
        probe = HealthProbe(host="0.0.0.0", port=9090)
        assert probe.host == "0.0.0.0"
        assert probe.port == 9090

    def test_extra_checks_stored(self):
        def check_fn():
            return {"db": {"status": "ok"}}

        probe = HealthProbe(extra_checks=check_fn)
        assert probe._extra_checks is check_fn

    def test_running_false_before_start(self):
        probe = HealthProbe(host="127.0.0.1", port=0)
        assert probe.running is False

    def test_default_host_fallback(self):
        """Empty string host defaults to 127.0.0.1."""
        probe = HealthProbe(host="", port=9999)
        assert probe.host == "127.0.0.1"


class TestHealthProbeStateSetters:
    """HealthProbe static state setters."""

    def setup_method(self):
        HealthHandler.server_state = {"liveness": True, "readiness": False, "startup": False}

    def test_set_liveness(self):
        HealthProbe.set_liveness(False)
        assert HealthHandler.server_state["liveness"] is False
        HealthProbe.set_liveness(True)
        assert HealthHandler.server_state["liveness"] is True

    def test_set_readiness(self):
        HealthProbe.set_readiness(True)
        assert HealthHandler.server_state["readiness"] is True

    def test_set_startup(self):
        HealthProbe.set_startup(True)
        assert HealthHandler.server_state["startup"] is True


class TestHealthHandlerLiveness:
    """HealthHandler /healthz and /livez liveness probe tests."""

    def setup_method(self):
        HealthHandler.server_state = {"liveness": True, "readiness": False, "startup": False}
        HealthHandler.extra_checks = None

    def test_healthz_alive(self):
        handler = _make_handler_for_test("GET", "/healthz")
        handler._handle_request()
        assert handler._status_code == 200
        body = handler.wfile.getvalue().decode()
        data = json.loads(body)
        assert data["status"] == "ok"

    def test_healthz_dead(self):
        HealthHandler.server_state["liveness"] = False
        handler = _make_handler_for_test("GET", "/healthz")
        handler._handle_request()
        assert handler._status_code == 503
        body = handler.wfile.getvalue().decode()
        data = json.loads(body)
        assert data["status"] == "down"

    def test_livez_alias(self):
        handler = _make_handler_for_test("GET", "/livez")
        handler._handle_request()
        assert handler._status_code == 200
        body = handler.wfile.getvalue().decode()
        assert json.loads(body)["status"] == "ok"

    def test_healthz_trailing_slash(self):
        handler = _make_handler_for_test("GET", "/healthz/")
        handler._handle_request()
        assert handler._status_code == 200


class TestHealthHandlerReadiness:
    """HealthHandler /readyz readiness probe tests."""

    def setup_method(self):
        HealthHandler.server_state = {"liveness": True, "readiness": False, "startup": False}
        HealthHandler.extra_checks = None

    def test_readyz_not_ready(self):
        handler = _make_handler_for_test("GET", "/readyz")
        handler._handle_request()
        assert handler._status_code == 503
        body = handler.wfile.getvalue().decode()
        data = json.loads(body)
        assert data["status"] == "not_ready"

    def test_readyz_ready_no_checks(self):
        HealthHandler.server_state["readiness"] = True
        handler = _make_handler_for_test("GET", "/readyz")
        handler._handle_request()
        assert handler._status_code == 200
        body = handler.wfile.getvalue().decode()
        data = json.loads(body)
        assert data["status"] == "ok"
        assert data["checks"] == {}

    def test_readyz_ready_with_passing_checks(self):
        HealthHandler.server_state["readiness"] = True
        HealthHandler.extra_checks = lambda: {
            "db": {"status": "ok"},
            "cache": {"status": "ok"},
        }
        handler = _make_handler_for_test("GET", "/readyz")
        handler._handle_request()
        assert handler._status_code == 200
        data = json.loads(handler.wfile.getvalue().decode())
        assert data["status"] == "ok"

    def test_readyz_failing_extra_checks(self):
        HealthHandler.server_state["readiness"] = True
        HealthHandler.extra_checks = lambda: {
            "db": {"status": "ok"},
            "cache": {"status": "degraded"},
        }
        handler = _make_handler_for_test("GET", "/readyz")
        handler._handle_request()
        assert handler._status_code == 503
        data = json.loads(handler.wfile.getvalue().decode())
        assert data["status"] == "not_ready"

    def test_readyz_extra_checks_raise(self):
        HealthHandler.server_state["readiness"] = True

        def bad_checks():
            raise RuntimeError("boom")

        HealthHandler.extra_checks = bad_checks
        handler = _make_handler_for_test("GET", "/readyz")
        handler._handle_request()
        assert handler._status_code == 503
        data = json.loads(handler.wfile.getvalue().decode())
        assert data["status"] == "not_ready"
        assert data["checks"] == {"error": "extra_checks_failed"}


class TestHealthHandlerStartup:
    """HealthHandler /startupz startup probe tests."""

    def setup_method(self):
        HealthHandler.server_state = {"liveness": True, "readiness": False, "startup": False}
        HealthHandler.extra_checks = None

    def test_startupz_not_started(self):
        handler = _make_handler_for_test("GET", "/startupz")
        handler._handle_request()
        assert handler._status_code == 503
        data = json.loads(handler.wfile.getvalue().decode())
        assert data["status"] == "starting_up"

    def test_startupz_started(self):
        HealthHandler.server_state["startup"] = True
        handler = _make_handler_for_test("GET", "/startupz")
        handler._handle_request()
        assert handler._status_code == 200
        data = json.loads(handler.wfile.getvalue().decode())
        assert data["status"] == "ok"


class TestHealthHandlerMisc:
    """HealthHandler — root, 404, HEAD method."""

    def setup_method(self):
        HealthHandler.server_state = {"liveness": True, "readiness": False, "startup": False}
        HealthHandler.extra_checks = None

    def test_root_path_strips_to_empty(self):
        """After rstrip('/'), '/' becomes '' which doesn't match '/' — falls to 404."""
        handler = _make_handler_for_test("GET", "/")
        handler._handle_request()
        # Source code does path.rstrip("/") then checks path == "/" —
        # "/".rstrip("/") == "" so it falls through to 404.
        assert handler._status_code == 404

    def test_unknown_path_404(self):
        handler = _make_handler_for_test("GET", "/nonexistent")
        handler._handle_request()
        assert handler._status_code == 404
        data = json.loads(handler.wfile.getvalue().decode())
        assert data["error"] == "not_found"

    def test_head_method_works(self):
        """HEAD requests should be handled same as GET."""
        handler = _make_handler_for_test("HEAD", "/healthz")
        handler._handle_request()
        assert handler._status_code == 200


class TestHealthProbeLifecycle:
    """HealthProbe start/stop lifecycle (mocked HTTPServer)."""

    def test_stop_without_start_is_noop(self):
        probe = HealthProbe(host="127.0.0.1", port=0)
        probe.stop()  # Should not raise
        assert probe.running is False

    def test_start_background_sets_httpd(self):
        probe = HealthProbe(host="127.0.0.1", port=9876)
        with patch("tradebot.monitoring.health.HTTPServer") as mock_httpd_cls:
            mock_httpd = MagicMock()
            mock_httpd_cls.return_value = mock_httpd
            probe.start_background()
            assert probe.running is True
            mock_httpd_cls.assert_called_once_with(
                ("127.0.0.1", 9876), HealthHandler
            )

    def test_stop_shuts_down_httpd(self):
        probe = HealthProbe(host="127.0.0.1", port=0)
        probe._httpd = MagicMock()
        probe.stop()
        probe._httpd.shutdown.assert_called_once()

    def test_running_reflects_httpd_state(self):
        probe = HealthProbe(host="127.0.0.1", port=0)
        assert probe.running is False
        probe._httpd = MagicMock()
        assert probe.running is True
        probe._httpd = None
        assert probe.running is False


# ═══════════════════════════════════════════════════════════════════
#  3. TradeTracker, TradeRecord, TradeStats
# ═══════════════════════════════════════════════════════════════════


# Helper to avoid UNIQUE constraint collisions from millisecond-granularity IDs.
def _open_trade_safe(tracker, signal, entry_price, symbol="XAUUSD", **kwargs):
    """Open a trade and sleep 2ms to ensure unique trade_id."""
    tid = tracker.open_trade(signal, entry_price, symbol=symbol, **kwargs)
    time.sleep(0.002)
    return tid


class TestTradeRecord:
    """TradeRecord dataclass creation and defaults."""

    def test_default_creation(self):
        rec = TradeRecord()
        assert rec.trade_id == ""
        assert rec.symbol == ""
        assert rec.action == ""
        assert rec.entry_price == 0.0
        assert rec.exit_price == 0.0
        assert rec.sl == 0.0
        assert rec.tp == 0.0
        assert rec.stake == 0.0
        assert rec.outcome == ""
        assert rec.pips == 0.0
        assert rec.profit_usd == 0.0
        assert rec.profit_idr == 0
        assert rec.open_time == ""
        assert rec.close_time == ""
        assert rec.source == ""
        assert rec.confidence == 0.0
        assert rec.grade == ""

    def test_creation_with_values(self):
        rec = TradeRecord(
            trade_id="tr_123",
            symbol="XAUUSD",
            action="BUY",
            entry_price=3350.0,
            exit_price=3360.0,
            sl=3340.0,
            tp=3370.0,
            stake=1.0,
            outcome="TP_HIT",
            pips=100.0,
            profit_usd=100.0,
            profit_idr=1635000,
            open_time="2025-01-01T00:00:00+07:00",
            close_time="2025-01-01T01:00:00+07:00",
            source="ai",
            confidence=0.85,
            grade="A",
        )
        assert rec.trade_id == "tr_123"
        assert rec.symbol == "XAUUSD"
        assert rec.action == "BUY"
        assert rec.outcome == "TP_HIT"
        assert rec.pips == 100.0

    def test_asdict_roundtrip(self):
        rec = TradeRecord(trade_id="tr_1", symbol="XAUUSD", action="BUY")
        d = asdict(rec)
        assert d["trade_id"] == "tr_1"
        assert d["symbol"] == "XAUUSD"
        assert isinstance(d, dict)


class TestTradeStats:
    """TradeStats dataclass creation and defaults."""

    def test_default_creation(self):
        stats = TradeStats()
        assert stats.total == 0
        assert stats.wins == 0
        assert stats.losses == 0
        assert stats.breakeven == 0
        assert stats.win_rate == 0.0
        assert stats.total_pips == 0.0
        assert stats.total_profit_usd == 0.0
        assert stats.total_profit_idr == 0
        assert stats.best_win_pips == 0.0
        assert stats.worst_loss_pips == 0.0
        assert stats.avg_win_pips == 0.0
        assert stats.avg_loss_pips == 0.0
        assert stats.max_consecutive_wins == 0
        assert stats.max_consecutive_losses == 0
        assert stats.open_positions == 0
        assert stats.current_streak == "none"
        assert stats.current_streak_count == 0

    def test_creation_with_values(self):
        stats = TradeStats(
            total=10,
            wins=7,
            losses=3,
            win_rate=70.0,
            total_pips=50.0,
            total_profit_usd=25.0,
            current_streak="win",
            current_streak_count=3,
        )
        assert stats.total == 10
        assert stats.wins == 7
        assert stats.win_rate == 70.0
        assert stats.current_streak == "win"


@pytest.fixture
def tracker(temp_db: str) -> TradeTracker:
    """Return a TradeTracker backed by a temp database."""
    return TradeTracker(db_path=Path(temp_db))


class TestTradeTrackerInit:
    """TradeTracker initialization and DB creation."""

    def test_creates_db_and_table(self, tracker: TradeTracker, temp_db: str):
        import sqlite3

        conn = sqlite3.connect(temp_db)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='trades'"
        )
        assert cursor.fetchone() is not None
        conn.close()

    def test_init_with_no_path_uses_settings(self):
        """TradeTracker() without db_path falls back to settings."""
        with patch("tradebot.monitoring.tracker.SQLiteStorage") as mock_storage:
            mock_inst = MagicMock()
            mock_storage.return_value = mock_inst
            TradeTracker()
            mock_storage.assert_called_once_with(None)
            assert mock_inst.execute.call_count >= 5  # init_db + migration + extra tables


class TestTradeTrackerOpenTrade:
    """TradeTracker.open_trade() tests."""

    def test_open_buy_trade(self, tracker: TradeTracker):
        signal = {"action": "BUY", "sl": 3340.0, "tp": 3370.0, "confidence": 0.8, "grade": "A"}
        trade_id = tracker.open_trade(signal, entry_price=3350.0, symbol="XAUUSD")
        assert trade_id is not None
        assert trade_id.startswith("tr_")

    def test_open_sell_trade(self, tracker: TradeTracker):
        signal = {"action": "SELL", "sl": 3360.0, "tp": 3330.0}
        trade_id = tracker.open_trade(signal, entry_price=3350.0, symbol="XAUUSD")
        assert trade_id is not None

    def test_open_trade_rejected_bad_action(self, tracker: TradeTracker):
        signal = {"action": "HOLD"}
        trade_id = tracker.open_trade(signal, entry_price=3350.0, symbol="XAUUSD")
        assert trade_id is None

    def test_open_trade_rejected_empty_signal(self, tracker: TradeTracker):
        trade_id = tracker.open_trade({}, entry_price=3350.0, symbol="XAUUSD")
        assert trade_id is None

    def test_open_trade_rejected_none_signal(self, tracker: TradeTracker):
        trade_id = tracker.open_trade(None, entry_price=3350.0, symbol="XAUUSD")  # type: ignore[arg-type]
        assert trade_id is None

    def test_open_trade_rejected_xau_out_of_range(self, tracker: TradeTracker):
        signal = {"action": "BUY", "sl": 0, "tp": 0}
        # Too low
        assert tracker.open_trade(signal, entry_price=100.0, symbol="XAUUSD") is None
        # Too high
        assert tracker.open_trade(signal, entry_price=99999.0, symbol="XAUUSD") is None

    def test_open_trade_rejected_btc_out_of_range(self, tracker: TradeTracker):
        signal = {"action": "BUY", "sl": 0, "tp": 0}
        assert tracker.open_trade(signal, entry_price=100.0, symbol="BTCUSD") is None
        assert tracker.open_trade(signal, entry_price=999999.0, symbol="BTCUSD") is None

    def test_open_trade_with_defaults(self, tracker: TradeTracker):
        signal = {"action": "CALL"}
        trade_id = tracker.open_trade(signal, entry_price=100.0, symbol="R_75")
        assert trade_id is not None
        open_trades = tracker.get_open_trades()
        assert len(open_trades) == 1
        assert open_trades[0].action == "CALL"
        assert open_trades[0].outcome == "OPEN"

    def test_open_trade_with_source_and_chat(self, tracker: TradeTracker):
        signal = {"action": "PUT", "sl": 0, "tp": 0}
        trade_id = tracker.open_trade(
            signal, entry_price=100.0, symbol="R_75", source="ai", chat_id="12345"
        )
        assert trade_id is not None


class TestTradeTrackerCloseTrade:
    """TradeTracker.close_trade() tests."""

    def test_close_trade_returns_record(self, tracker: TradeTracker):
        signal = {"action": "BUY", "sl": 3340.0, "tp": 3370.0}
        trade_id = tracker.open_trade(signal, entry_price=3350.0, symbol="XAUUSD")
        assert trade_id is not None

        record = tracker.close_trade(trade_id, close_price=3360.0, outcome="MANUAL")
        assert record is not None
        assert record.trade_id == trade_id
        assert record.exit_price == 3360.0
        assert record.outcome == "MANUAL"
        assert record.pips > 0  # BUY from 3350 to 3360 should be positive

    def test_close_trade_not_found(self, tracker: TradeTracker):
        record = tracker.close_trade("nonexistent_id", close_price=100.0)
        assert record is None

    def test_close_trade_already_closed(self, tracker: TradeTracker):
        signal = {"action": "BUY", "sl": 3340.0, "tp": 3370.0}
        trade_id = tracker.open_trade(signal, entry_price=3350.0, symbol="XAUUSD")
        tracker.close_trade(trade_id, close_price=3360.0, outcome="MANUAL")
        # Second close should fail (no longer OPEN)
        record = tracker.close_trade(trade_id, close_price=3370.0, outcome="MANUAL")
        assert record is None

    def test_close_trade_pip_calculation_buy(self, tracker: TradeTracker):
        signal = {"action": "BUY", "sl": 0, "tp": 0}
        trade_id = tracker.open_trade(signal, entry_price=3350.0, symbol="XAUUSD")
        record = tracker.close_trade(trade_id, close_price=3351.0, outcome="MANUAL")
        # XAUUSD pip size = 0.1, so 1.0 / 0.1 = 10 pips
        assert record is not None
        assert record.pips == pytest.approx(10.0, abs=0.1)

    def test_close_trade_pip_calculation_sell(self, tracker: TradeTracker):
        signal = {"action": "SELL", "sl": 0, "tp": 0}
        trade_id = tracker.open_trade(signal, entry_price=3350.0, symbol="XAUUSD")
        record = tracker.close_trade(trade_id, close_price=3340.0, outcome="MANUAL")
        assert record is not None
        # SELL from 3350, close at 3340: diff = 10, pips = 10/0.1 = 100
        assert record.pips == pytest.approx(100.0, abs=0.1)

    def test_close_trade_breakeven(self, tracker: TradeTracker):
        signal = {"action": "BUY", "sl": 0, "tp": 0}
        trade_id = tracker.open_trade(signal, entry_price=3350.0, symbol="XAUUSD")
        record = tracker.close_trade(trade_id, close_price=3350.0, outcome="BREAKEVEN")
        assert record is not None
        assert record.outcome == "BREAKEVEN"
        assert record.pips == pytest.approx(0.0, abs=0.1)


class TestTradeTrackerCheckOutcomes:
    """TradeTracker.check_outcomes() TP/SL detection logic."""

    def test_tp_hit_buy(self, tracker: TradeTracker):
        signal = {"action": "BUY", "sl": 3340.0, "tp": 3370.0}
        trade_id = tracker.open_trade(signal, entry_price=3350.0, symbol="XAUUSD")

        closed = tracker.check_outcomes({"XAUUSD": 3375.0})
        assert len(closed) == 1
        assert closed[0].outcome == "TP_HIT"
        assert closed[0].trade_id == trade_id

    def test_sl_hit_buy(self, tracker: TradeTracker):
        signal = {"action": "BUY", "sl": 3340.0, "tp": 3370.0}
        trade_id = tracker.open_trade(signal, entry_price=3350.0, symbol="XAUUSD")

        closed = tracker.check_outcomes({"XAUUSD": 3335.0})
        assert len(closed) == 1
        assert closed[0].outcome == "SL_HIT"
        assert closed[0].trade_id == trade_id

    def test_tp_hit_sell(self, tracker: TradeTracker):
        signal = {"action": "SELL", "sl": 3360.0, "tp": 3330.0}
        trade_id = tracker.open_trade(signal, entry_price=3350.0, symbol="XAUUSD")

        closed = tracker.check_outcomes({"XAUUSD": 3325.0})
        assert len(closed) == 1
        assert closed[0].outcome == "TP_HIT"
        assert closed[0].trade_id == trade_id

    def test_sl_hit_sell(self, tracker: TradeTracker):
        signal = {"action": "SELL", "sl": 3360.0, "tp": 3330.0}
        tracker.open_trade(signal, entry_price=3350.0, symbol="XAUUSD")

        closed = tracker.check_outcomes({"XAUUSD": 3365.0})
        assert len(closed) == 1
        assert closed[0].outcome == "SL_HIT"

    def test_no_hit_stays_open(self, tracker: TradeTracker):
        signal = {"action": "BUY", "sl": 3340.0, "tp": 3370.0}
        tracker.open_trade(signal, entry_price=3350.0, symbol="XAUUSD")

        closed = tracker.check_outcomes({"XAUUSD": 3355.0})
        assert len(closed) == 0
        assert len(tracker.get_open_trades()) == 1

    def test_check_outcomes_empty_prices(self, tracker: TradeTracker):
        signal = {"action": "BUY", "sl": 3340.0, "tp": 3370.0}
        tracker.open_trade(signal, entry_price=3350.0, symbol="XAUUSD")
        assert tracker.check_outcomes({}) == []
        assert tracker.check_outcomes(None) == []

    def test_check_outcomes_multiple_trades(self, tracker: TradeTracker):
        _open_trade_safe(
            tracker, {"action": "BUY", "sl": 3340.0, "tp": 3370.0}, 3350.0, "XAUUSD"
        )
        _open_trade_safe(
            tracker, {"action": "SELL", "sl": 105.0, "tp": 95.0}, 100.0, "EURUSD"
        )

        closed = tracker.check_outcomes({"XAUUSD": 3375.0, "EURUSD": 94.0})
        assert len(closed) == 2
        outcomes = {c.outcome for c in closed}
        assert outcomes == {"TP_HIT"}

    def test_call_put_tp_sl(self, tracker: TradeTracker):
        """CALL/PUT should behave like BUY/SELL for TP/SL."""
        _open_trade_safe(
            tracker, {"action": "CALL", "sl": 95.0, "tp": 105.0}, 100.0, "R_75"
        )
        _open_trade_safe(
            tracker, {"action": "PUT", "sl": 105.0, "tp": 95.0}, 100.0, "R_100"
        )

        closed = tracker.check_outcomes({"R_75": 106.0, "R_100": 94.0})
        assert len(closed) == 2
        for c in closed:
            assert c.outcome == "TP_HIT"


class TestTradeTrackerGetStats:
    """TradeTracker.get_stats() aggregation tests."""

    def test_stats_empty(self, tracker: TradeTracker):
        stats = tracker.get_stats()
        assert stats.total == 0
        assert stats.wins == 0
        assert stats.losses == 0
        assert stats.win_rate == 0.0
        assert stats.open_positions == 0

    def test_stats_after_wins_and_losses(self, tracker: TradeTracker):
        for _ in range(2):
            tid = _open_trade_safe(
                tracker, {"action": "BUY", "sl": 3340.0, "tp": 3370.0}, 3350.0, "XAUUSD"
            )
            tracker.close_trade(tid, close_price=3370.0, outcome="TP_HIT")

        tid = _open_trade_safe(
            tracker, {"action": "BUY", "sl": 3340.0, "tp": 3370.0}, 3350.0, "XAUUSD"
        )
        tracker.close_trade(tid, close_price=3340.0, outcome="SL_HIT")

        stats = tracker.get_stats()
        assert stats.total == 3
        assert stats.wins == 2
        assert stats.losses == 1
        assert stats.win_rate == pytest.approx(66.7, abs=0.1)

    def test_stats_breakeven(self, tracker: TradeTracker):
        tid = tracker.open_trade(
            {"action": "BUY", "sl": 3340.0, "tp": 3370.0}, 3350.0, "XAUUSD"
        )
        tracker.close_trade(tid, close_price=3350.0, outcome="BREAKEVEN")

        stats = tracker.get_stats()
        assert stats.total == 1
        assert stats.breakeven == 1
        assert stats.wins == 0
        assert stats.losses == 0

    def test_stats_open_positions_counted(self, tracker: TradeTracker):
        _open_trade_safe(
            tracker, {"action": "BUY", "sl": 3340.0, "tp": 3370.0}, 3350.0, "XAUUSD"
        )
        _open_trade_safe(
            tracker, {"action": "SELL", "sl": 3360.0, "tp": 3330.0}, 3350.0, "XAUUSD"
        )

        stats = tracker.get_stats()
        assert stats.open_positions == 2
        assert stats.total == 0  # No closed trades

    def test_stats_streaks(self, tracker: TradeTracker):
        for _ in range(3):
            tid = _open_trade_safe(
                tracker, {"action": "BUY", "sl": 3340.0, "tp": 3370.0}, 3350.0, "XAUUSD"
            )
            tracker.close_trade(tid, close_price=3370.0, outcome="TP_HIT")

        for _ in range(2):
            tid = _open_trade_safe(
                tracker, {"action": "BUY", "sl": 3340.0, "tp": 3370.0}, 3350.0, "XAUUSD"
            )
            tracker.close_trade(tid, close_price=3340.0, outcome="SL_HIT")

        stats = tracker.get_stats()
        assert stats.max_consecutive_wins == 3
        assert stats.max_consecutive_losses == 2
        assert stats.current_streak == "loss"
        assert stats.current_streak_count == 2

    def test_stats_best_worst_pips(self, tracker: TradeTracker):
        tid = _open_trade_safe(
            tracker, {"action": "BUY", "sl": 3340.0, "tp": 3370.0}, 3350.0, "XAUUSD"
        )
        tracker.close_trade(tid, close_price=3360.0, outcome="TP_HIT")

        tid = _open_trade_safe(
            tracker, {"action": "BUY", "sl": 3340.0, "tp": 3370.0}, 3350.0, "XAUUSD"
        )
        tracker.close_trade(tid, close_price=3340.0, outcome="SL_HIT")

        stats = tracker.get_stats()
        assert stats.best_win_pips > 0
        assert stats.worst_loss_pips > 0

    def test_stats_profit_accumulation(self, tracker: TradeTracker):
        tid = tracker.open_trade(
            {"action": "BUY", "sl": 3340.0, "tp": 3370.0}, 3350.0, "XAUUSD"
        )
        tracker.close_trade(tid, close_price=3355.0, outcome="TP_HIT")

        stats = tracker.get_stats()
        assert stats.total_profit_usd > 0
        assert stats.total_profit_idr > 0


class TestTradeTrackerRecentAndOpen:
    """get_recent_trades() and get_open_trades() tests."""

    def test_get_recent_trades_empty(self, tracker: TradeTracker):
        trades = tracker.get_recent_trades(limit=5)
        assert trades == []

    def test_get_recent_trades(self, tracker: TradeTracker):
        tid1 = tracker.open_trade(
            {"action": "BUY", "sl": 3340.0, "tp": 3370.0}, 3350.0, "XAUUSD"
        )
        tracker.close_trade(tid1, close_price=3360.0, outcome="TP_HIT")
        time.sleep(0.002)

        tid2 = tracker.open_trade(
            {"action": "SELL", "sl": 3360.0, "tp": 3330.0}, 3350.0, "XAUUSD"
        )
        tracker.close_trade(tid2, close_price=3340.0, outcome="SL_HIT")

        trades = tracker.get_recent_trades(limit=10)
        assert len(trades) == 2
        assert all(isinstance(tr, TradeRecord) for tr in trades)
        # Most recent first
        assert trades[0].trade_id == tid2

    def test_get_open_trades(self, tracker: TradeTracker):
        _open_trade_safe(
            tracker, {"action": "BUY", "sl": 3340.0, "tp": 3370.0}, 3350.0, "XAUUSD"
        )
        _open_trade_safe(
            tracker, {"action": "SELL", "sl": 3360.0, "tp": 3330.0}, 3350.0, "XAUUSD"
        )

        open_trades = tracker.get_open_trades()
        assert len(open_trades) == 2
        assert all(tr.outcome == "OPEN" for tr in open_trades)

    def test_get_open_trades_empty(self, tracker: TradeTracker):
        assert tracker.get_open_trades() == []


class TestTradeTrackerDailyTrades:
    """TradeTracker.get_daily_trades() tests."""

    def test_daily_trades_empty(self, tracker: TradeTracker):
        result = tracker.get_daily_trades("2099-01-01")
        assert result["date"] == "2099-01-01"
        assert result["trades"] == []
        assert result["total_signals"] == 0
        assert result["wins"] == 0
        assert result["losses"] == 0

    def test_daily_trades_with_data(self, tracker: TradeTracker):
        tid = tracker.open_trade(
            {"action": "BUY", "sl": 3340.0, "tp": 3370.0}, 3350.0, "XAUUSD"
        )
        tracker.close_trade(tid, close_price=3360.0, outcome="TP_HIT")

        wib = timezone(timedelta(hours=7))
        today = datetime.now(wib).strftime("%Y-%m-%d")

        result = tracker.get_daily_trades(today)
        assert result["total_signals"] >= 1
        assert result["wins"] >= 1
        assert "pairs" in result
        assert "XAUUSD" in result["pairs"]

    def test_daily_trades_default_date(self, tracker: TradeTracker):
        """Default date should be today (WIB)."""
        result = tracker.get_daily_trades()
        assert result["date"]  # Should be non-empty


class TestTradeTrackerFormatWinrate:
    """TradeTracker.format_winrate() Telegram output tests."""

    def test_format_winrate_empty(self, tracker: TradeTracker):
        text = tracker.format_winrate()
        assert "TRADE PERFORMANCE" in text
        assert "Win Rate:" in text
        assert "0.0%" in text

    def test_format_winrate_with_trades(self, tracker: TradeTracker):
        for _ in range(3):
            tid = _open_trade_safe(
                tracker, {"action": "BUY", "sl": 3340.0, "tp": 3370.0}, 3350.0, "XAUUSD"
            )
            tracker.close_trade(tid, close_price=3360.0, outcome="TP_HIT")

        tid = _open_trade_safe(
            tracker, {"action": "BUY", "sl": 3340.0, "tp": 3370.0}, 3350.0, "XAUUSD"
        )
        tracker.close_trade(tid, close_price=3340.0, outcome="SL_HIT")

        text = tracker.format_winrate()
        assert "75.0%" in text
        assert "3W" in text
        assert "1L" in text
        assert "Total Trades: 4" in text

    def test_format_winrate_contains_html(self, tracker: TradeTracker):
        text = tracker.format_winrate()
        assert "<b>" in text

    def test_format_winrate_performance_emoji(self, tracker: TradeTracker):
        tid = tracker.open_trade(
            {"action": "BUY", "sl": 3340.0, "tp": 3370.0}, 3350.0, "XAUUSD"
        )
        tracker.close_trade(tid, close_price=3340.0, outcome="SL_HIT")

        text = tracker.format_winrate()
        assert "\U0001f534" in text  # red circle emoji for < 40% win rate


class TestTradeTrackerFormatHistory:
    """TradeTracker.format_history() Telegram output tests."""

    def test_format_history_empty(self, tracker: TradeTracker):
        text = tracker.format_history(limit=10)
        assert "No trade history" in text

    def test_format_history_with_trades(self, tracker: TradeTracker):
        tid = tracker.open_trade(
            {"action": "BUY", "sl": 3340.0, "tp": 3370.0}, 3350.0, "XAUUSD"
        )
        tracker.close_trade(tid, close_price=3360.0, outcome="TP_HIT")

        text = tracker.format_history(limit=10)
        assert "TRADE HISTORY" in text
        assert "XAUUSD" in text
        assert "\u2705" in text  # check mark emoji

    def test_format_history_limit(self, tracker: TradeTracker):
        for _ in range(5):
            tid = _open_trade_safe(
                tracker, {"action": "BUY", "sl": 3340.0, "tp": 3370.0}, 3350.0, "XAUUSD"
            )
            tracker.close_trade(tid, close_price=3360.0, outcome="TP_HIT")

        text = tracker.format_history(limit=3)
        # Should have exactly 3 trade entries (each is 3 lines)
        lines = text.split("\n")
        # Header = 2 lines, then 3 trades x 3 lines = 11
        assert len(lines) <= 11

    def test_format_history_contains_emoji(self, tracker: TradeTracker):
        tid = tracker.open_trade(
            {"action": "SELL", "sl": 3360.0, "tp": 3330.0}, 3350.0, "XAUUSD"
        )
        tracker.close_trade(tid, close_price=3340.0, outcome="SL_HIT")

        text = tracker.format_history()
        assert "\u274c" in text  # cross mark emoji


class TestTradeTrackerPipCalculations:
    """TradeTracker static pip calculation helpers."""

    def test_pip_size_xauusd(self):
        assert TradeTracker._pip_size("XAUUSD") == 0.1
        assert TradeTracker._pip_size("GOLD") == 0.1

    def test_pip_size_btcusd(self):
        assert TradeTracker._pip_size("BTCUSD") == 1.0
        assert TradeTracker._pip_size("BTC") == 1.0

    def test_pip_size_ethusd(self):
        assert TradeTracker._pip_size("ETHUSD") == 0.01

    def test_pip_size_jpy_pair(self):
        assert TradeTracker._pip_size("USDJPY") == 0.01

    def test_pip_size_default_forex(self):
        assert TradeTracker._pip_size("EURUSD") == 0.0001

    def test_pip_value_xauusd(self):
        assert TradeTracker._pip_value("XAUUSD") == 1.0

    def test_pip_value_btcusd(self):
        assert TradeTracker._pip_value("BTCUSD") == 1.0

    def test_pip_value_default_forex(self):
        assert TradeTracker._pip_value("EURUSD") == 10.0

    def test_compute_pips_buy_profit(self):
        pips = TradeTracker._compute_pips(3360.0, 3350.0, "BUY", "XAUUSD")
        assert pips == pytest.approx(100.0, abs=0.1)  # 10 / 0.1

    def test_compute_pips_buy_loss(self):
        pips = TradeTracker._compute_pips(3340.0, 3350.0, "BUY", "XAUUSD")
        assert pips == pytest.approx(-100.0, abs=0.1)

    def test_compute_pips_sell_profit(self):
        pips = TradeTracker._compute_pips(3340.0, 3350.0, "SELL", "XAUUSD")
        assert pips == pytest.approx(100.0, abs=0.1)

    def test_compute_pips_sell_loss(self):
        pips = TradeTracker._compute_pips(3360.0, 3350.0, "SELL", "XAUUSD")
        assert pips == pytest.approx(-100.0, abs=0.1)

    def test_compute_pips_call(self):
        # EURUSD pip_size = 0.0001, so 1.0 / 0.0001 = 10000 pips
        pips = TradeTracker._compute_pips(101.0, 100.0, "CALL", "EURUSD")
        assert pips == pytest.approx(10000.0, abs=0.1)

    def test_compute_pips_put(self):
        # EURUSD pip_size = 0.0001, so 1.0 / 0.0001 = 10000 pips
        pips = TradeTracker._compute_pips(99.0, 100.0, "PUT", "EURUSD")
        assert pips == pytest.approx(10000.0, abs=0.1)
