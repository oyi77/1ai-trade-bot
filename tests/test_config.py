"""Tests for configuration loading, env override, and defaults."""

from __future__ import annotations

from tradebot.config.settings import Settings


class TestSettingsDefaults:
    """Verify that default values match expectations."""

    def test_deriv_defaults(self):
        s = Settings()
        # App ID might be set in environment; check it's a string
        assert isinstance(s.DERIV_APP_ID, str)
        assert s.DERIV_MODE == "demo"
        assert s.DERIV_SYMBOL == "R_75"
        assert s.DERIV_INITIAL_STAKE == 0.35
        assert s.DERIV_STAKE_MULTIPLIER == 1.55
        assert s.DERIV_MAX_OPS == 3
        assert s.DERIV_DURATION == 1
        assert s.DERIV_DURATION_UNIT == "t"
        assert s.DERIV_TICK_HISTORY == 100
        assert s.DERIV_MIN_CONFIDENCE == 0.3

    def test_broker_defaults(self):
        s = Settings()
        assert s.BROKER_DRY_RUN is True
        assert s.BROKER_MAX_POSITIONS == 1
        assert s.BROKER_DEFAULT_STAKE == 0.35
        assert s.BROKER_RECONNECT_DELAY == 5
        assert s.BROKER_RECONNECT_MAX_RETRIES == 10

    def test_risk_defaults(self):
        s = Settings()
        assert s.DAILY_TAKE_PROFIT == 5.0
        assert s.DAILY_STOP_LOSS == -8.0
        assert s.CONFIG_L_SL_POINTS == 32.0
        assert s.CONFIG_L_TP_POINTS == 52.0
        assert s.CONFIG_L_RR == 1.625

    def test_monitoring_defaults(self):
        s = Settings()
        assert s.MONITORING_HEARTBEAT_INTERVAL == 60
        assert s.MONITORING_PROMETHEUS_ENABLED is False
        assert s.MONITORING_PROMETHEUS_PORT == 8000
        assert s.MONITORING_HEALTH_LOG is True

    def test_engine_defaults(self):
        s = Settings()
        assert s.ENGINE_CONSENSUS_MIN_VOTES == 2
        assert s.ENGINE_CONSENSUS_WEIGHTED is True
        assert s.ENGINE_CONFIDENCE_THRESHOLD == 0.5
        assert s.ENGINE_CACHE_RESULTS is True

    def test_signal_defaults(self):
        s = Settings()
        assert s.SIGNAL_MIN_CONFIDENCE == 0.3
        assert s.SIGNAL_VALIDATION_STRICT is True
        assert s.SIGNAL_DEDUP_WINDOW == 60
        assert s.SIGNAL_QUEUE_MAXSIZE == 100

    def test_connection_defaults(self):
        s = Settings()
        assert s.WS_PING_INTERVAL == 20
        assert s.WS_PING_TIMEOUT == 10
        assert s.WS_TIMEOUT == 15

    def test_bridge_defaults(self):
        s = Settings()
        assert s.BRIDGE_HOST == "0.0.0.0"
        assert s.BRIDGE_PORT == 8082

    def test_pattern_defaults(self):
        s = Settings()
        assert s.TARGET_CARRIERS == "1,2,3,4"
        assert s.MAX_JARING_TICKS == 3
        assert s.ANTI_FLOOD_WINDOW == 20
        assert s.ANTI_FLOOD_MAX == 3

    def test_binance_defaults(self):
        s = Settings()
        assert s.BINANCE_BASE_URL == "https://api.binance.com"
        assert s.BINANCE_TIMEOUT == 15


class TestSettingsEnvOverride:
    """Verify environment variable overrides work."""

    def test_env_override_string(self, monkeypatch):
        monkeypatch.setenv("DERIV_SYMBOL", "R_100")
        s = Settings()
        assert s.DERIV_SYMBOL == "R_100"

    def test_env_override_int(self, monkeypatch):
        monkeypatch.setenv("DERIV_MAX_OPS", "5")
        s = Settings()
        assert s.DERIV_MAX_OPS == 5

    def test_env_override_float(self, monkeypatch):
        monkeypatch.setenv("DERIV_INITIAL_STAKE", "1.00")
        s = Settings()
        assert s.DERIV_INITIAL_STAKE == 1.00

    def test_env_override_bool_true(self, monkeypatch):
        monkeypatch.setenv("BROKER_DRY_RUN", "False")
        s = Settings()
        assert s.BROKER_DRY_RUN is False

    def test_env_override_bridge_port(self, monkeypatch):
        monkeypatch.setenv("BRIDGE_PORT", "9090")
        s = Settings()
        assert s.BRIDGE_PORT == 9090

    def test_env_override_daily_limits(self, monkeypatch):
        monkeypatch.setenv("DAILY_TAKE_PROFIT", "10.0")
        monkeypatch.setenv("DAILY_STOP_LOSS", "-15.0")
        s = Settings()
        assert s.DAILY_TAKE_PROFIT == 10.0
        assert s.DAILY_STOP_LOSS == -15.0

    def test_env_override_ignores_extra(self):
        """Settings should ignore unknown env vars (extra='ignore')."""
        import os

        os.environ["SOME_RANDOM_VAR"] = "hello"
        s = Settings()
        assert hasattr(s, "DERIV_SYMBOL")  # just verify it loaded
        del os.environ["SOME_RANDOM_VAR"]

    def test_env_override_lowercase(self, monkeypatch):
        """Settings should be case-insensitive for env var names."""
        monkeypatch.setenv("deriv_symbol", "R_25")
        s = Settings()
        assert s.DERIV_SYMBOL == "R_25"
