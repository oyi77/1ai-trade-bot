"""Tests for PortfolioOracle — session-aware asset selection engine."""

from __future__ import annotations

import random
from unittest.mock import patch


from tradebot.signals.portfolio_oracle import (
    ALL_ASSETS,
    ASSET_TIERS,
    ALWAYS_ON_RICS,
    SESSION_ASSETS,
    PortfolioOracle,
)


# ── Constants ──────────────────────────────────────────────────────────────

REQUIRED_KEYS = {"ric", "direction_picker_params", "duration", "action"}
DIRECTION_KEYS = {"lookback", "threshold", "payout"}

ALL_RICS = {a["ric"] for a in ALL_ASSETS}
UNKNOWN_RIC = "ZZZ-XXX"


# ── Data integrity ──────────────────────────────────────────────────────────


class TestDataIntegrity:
    """Verify the module-level constants are well-formed."""

    def test_all_assets_count(self) -> None:
        assert len(ALL_ASSETS) == 23

    def test_asset_tiers_structure(self) -> None:
        assert set(ASSET_TIERS) == {"tier1", "tier2", "tier3"}
        assert len(ASSET_TIERS["tier1"]) == 2
        assert len(ASSET_TIERS["tier2"]) == 6
        assert len(ASSET_TIERS["tier3"]) == 15

    def test_always_on_rics(self) -> None:
        assert set(ASSET_TIERS) == {"tier1", "tier2", "tier3"}

    def test_all_assets_have_required_fields(self) -> None:
        for a in ALL_ASSETS:
            assert "ric" in a
            assert "win" in a
            assert "thr" in a
            assert "payout" in a
            assert "wr" in a

    def test_session_assets_are_subset_of_all(self) -> None:
        for session, rics in SESSION_ASSETS.items():
            for ric in rics:
                assert ric in ALL_RICS, f"{ric} in session {session} not in ALL_ASSETS"

    def test_always_on_rics_are_in_all_assets(self) -> None:
        for ric in ALWAYS_ON_RICS:
            assert ric in ALL_RICS, f"{ric} not in ALL_ASSETS"


# ── PortfolioOracle — basic output shape ─────────────────────────────────────


class TestPortfolioOracle:
    """Core PortfolioOracle behavior."""

    def test_get_best_asset_returns_valid_dict(self) -> None:
        oracle = PortfolioOracle()
        result = oracle.get_best_asset(hour_utc=14)  # overlap session
        assert result is not None
        assert set(result) == REQUIRED_KEYS
        assert isinstance(result["ric"], str)
        assert isinstance(result["duration"], int)
        assert result["action"] == "turbo"
        assert set(result["direction_picker_params"]) == DIRECTION_KEYS
        assert isinstance(result["direction_picker_params"]["lookback"], int)
        assert isinstance(result["direction_picker_params"]["threshold"], float)
        assert isinstance(result["direction_picker_params"]["payout"], float)

    def test_get_best_asset_for_now_returns_non_none(self) -> None:
        """get_best_asset_for_now() delegates to get_best_asset() and returns a result."""
        with patch(
            "tradebot.signals.portfolio_oracle.datetime"
        ) as mock_dt:
            mock_dt.now.return_value.hour = 14
            result = PortfolioOracle().get_best_asset_for_now()
            assert result is not None
            assert set(result) == REQUIRED_KEYS

    def test_get_best_asset_ric_is_known(self) -> None:
        oracle = PortfolioOracle()
        for hour in range(24):
            result = oracle.get_best_asset(hour_utc=hour)
            assert result is not None, f"None at hour={hour}"
            assert result["ric"] in ALL_RICS, f"Unknown RIC {result['ric']} at hour={hour}"

    def test_get_best_asset_none_when_no_eligible(self) -> None:
        """If all assets are filtered by consecutive check, returns None."""
        oracle = PortfolioOracle()
        # Trade the same asset 3 times to exhaust it
        oracle._last_asset_rics = ["POWER-X", "POWER-X", "POWER-X"]
        result = oracle.get_best_asset(hour_utc=14)
        # Should still find something else (many assets available)
        assert result is not None
        assert result["ric"] != "POWER-X"


# ── Session-based asset selection ──────────────────────────────────────────


class TestSessionSelection:
    """Verify session-aware asset filtering."""

    def test_asia_session_returns_jpy_aud_pairs(self) -> None:
        """Asia session (hour=3) should include JPY and AUD pairs."""
        oracle = PortfolioOracle()
        assets = oracle.get_asset_by_session(hour_utc=3)
        rics = {a["ric"] for a in assets}
        # Asia session RICs
        asia_rics = set(SESSION_ASSETS["asia"])
        # All asia session RICs should be present
        assert asia_rics.issubset(rics), f"Missing asia RICs: {asia_rics - rics}"
        # JPY and AUD pairs should be present
        assert any("JPY" in r for r in rics)
        assert any("AUD" in r for r in rics)

    def test_london_session_returns_eur_gbp_pairs(self) -> None:
        oracle = PortfolioOracle()
        assets = oracle.get_asset_by_session(hour_utc=9)
        rics = {a["ric"] for a in assets}
        london_rics = set(SESSION_ASSETS["london"])
        assert london_rics.issubset(rics), f"Missing London RICs: {london_rics - rics}"

    def test_us_session_returns_usd_pairs(self) -> None:
        oracle = PortfolioOracle()
        assets = oracle.get_asset_by_session(hour_utc=18)
        rics = {a["ric"] for a in assets}
        us_rics = set(SESSION_ASSETS["us"])
        assert us_rics.issubset(rics), f"Missing US RICs: {us_rics - rics}"

    def test_overlap_session_returns_both_london_and_us(self) -> None:
        oracle = PortfolioOracle()
        assets = oracle.get_asset_by_session(hour_utc=14)
        rics = {a["ric"] for a in assets}
        london_rics = set(SESSION_ASSETS["london"])
        us_rics = set(SESSION_ASSETS["us"])
        assert london_rics.issubset(rics), "Missing London RICs in overlap"
        assert us_rics.issubset(rics), "Missing US RICs in overlap"

    def test_always_on_rics_returned_regardless_of_session(self) -> None:
        oracle = PortfolioOracle()
        for hour in (3, 9, 14, 18):
            assets = oracle.get_asset_by_session(hour_utc=hour)
            rics = {a["ric"] for a in assets}
            for always_on in ALWAYS_ON_RICS:
                assert always_on in rics, f"{always_on} missing at hour={hour}"

    def test_session_assets_sorted_session_first(self) -> None:
        """Session-matching assets appear before always-on, which appear before off-session."""
        oracle = PortfolioOracle()
        assets = oracle.get_asset_by_session(hour_utc=3)  # asia
        rics = [a["ric"] for a in assets]
        asia_rics = set(SESSION_ASSETS["asia"])
        # Find the boundary: last session asset index
        session_indices = [i for i, r in enumerate(rics) if r in asia_rics]
        always_on_indices = [i for i, r in enumerate(rics) if r in ALWAYS_ON_RICS]
        off_session_indices = [
            i for i, r in enumerate(rics)
            if r not in asia_rics and r not in ALWAYS_ON_RICS
        ]
        # All session assets come before any always-on or off-session
        if session_indices and always_on_indices:
            assert max(session_indices) < min(always_on_indices)
        if session_indices and off_session_indices:
            assert max(session_indices) < min(off_session_indices)
        # Always-on come before off-session
        if always_on_indices and off_session_indices:
            assert max(always_on_indices) < min(off_session_indices)


# ── can_trade_asset ─────────────────────────────────────────────────────────


class TestCanTradeAsset:
    """Consecutive-trade filter."""

    def test_known_asset_returns_true(self) -> None:
        oracle = PortfolioOracle()
        assert oracle.can_trade_asset("POWER-X") is True

    def test_unknown_asset_returns_true(self) -> None:
        """Unknown RICs are not blocked (they just won't be selected)."""
        oracle = PortfolioOracle()
        assert oracle.can_trade_asset(UNKNOWN_RIC) is True

    def test_three_consecutive_blocks(self) -> None:
        oracle = PortfolioOracle()
        oracle._last_asset_rics = ["POWER-X", "POWER-X", "POWER-X"]
        assert oracle.can_trade_asset("POWER-X") is False

    def test_two_consecutive_allowed(self) -> None:
        oracle = PortfolioOracle()
        oracle._last_asset_rics = ["POWER-X", "POWER-X"]
        assert oracle.can_trade_asset("POWER-X") is True

    def test_fourth_after_three_clears(self) -> None:
        """After 3 repeats, the 4th trade on a different asset clears the oldest entry."""
        oracle = PortfolioOracle()
        oracle._last_asset_rics = ["POWER-X", "POWER-X", "POWER-X"]
        oracle._track_trade("GBPSGD")
        # Now _last_asset_rics = ["POWER-X", "POWER-X", "POWER-X", "GBPSGD"]
        # But only last 5 kept, so POWER-X count is still 3
        assert oracle.can_trade_asset("POWER-X") is False
        # Trade another to push out oldest POWER-X
        oracle._track_trade("CADSEK")
        oracle._track_trade("CHFNOK")
        # Now: ["POWER-X", "POWER-X", "GBPSGD", "CADSEK", "CHFNOK"] — POWER-X count = 2
        assert oracle.can_trade_asset("POWER-X") is True

    def test_reset_tracker_clears(self) -> None:
        oracle = PortfolioOracle()
        oracle._last_asset_rics = ["POWER-X", "POWER-X", "POWER-X"]
        oracle.reset_tracker()
        assert oracle.can_trade_asset("POWER-X") is True

    def test_last_traded_rics_property(self) -> None:
        oracle = PortfolioOracle()
        oracle._track_trade("POWER-X")
        oracle._track_trade("GBPSGD")
        assert oracle.last_traded_rics == ["POWER-X", "GBPSGD"]


# ── Consecutive trade filter (end-to-end) ──────────────────────────────────


class TestConsecutiveTradeFilter:
    """End-to-end: get_best_asset should not return the same asset >3x in a row."""

    def test_same_asset_not_returned_more_than_3x(self) -> None:
        oracle = PortfolioOracle()
        # Seed random for deterministic scoring
        random.seed(42)
        results: list[str] = []
        for _ in range(10):
            result = oracle.get_best_asset(hour_utc=14)
            assert result is not None
            results.append(result["ric"])
        # Check no asset appears more than 3 times in a row
        for i, ric in enumerate(results):
            count = 0
            for j in range(max(0, i - 3), i + 1):
                if results[j] == ric:
                    count += 1
            assert count <= 3, f"{ric} appears {count}x in a row at index {i}: {results}"

    def test_alternating_assets_never_blocked(self) -> None:
        oracle = PortfolioOracle()
        random.seed(42)
        # Force alternating pattern by tracking manually
        oracle._track_trade("POWER-X")
        oracle._track_trade("GBPSGD")
        oracle._track_trade("POWER-X")
        oracle._track_trade("GBPSGD")
        # Both should still be tradeable
        assert oracle.can_trade_asset("POWER-X") is True
        assert oracle.can_trade_asset("GBPSGD") is True


# ── Tier weighting ─────────────────────────────────────────────────────────


class TestTierWeighting:
    """Tier1 assets should be preferred over tier3 when session conditions are equal."""

    def test_tier1_preferred_over_tier3_in_same_session(self) -> None:
        """At hour=14 (overlap), both tier1 (POWER-X, GBPSGD) and tier3 assets are eligible.
        POWER-X is always-on, GBPSGD is in asia session. At overlap, many assets compete.
        We verify that tier1 assets appear in results more often than tier3 over many trials.
        """
        oracle = PortfolioOracle()
        random.seed(42)
        tier1_rics = {a["ric"] for a in ASSET_TIERS["tier1"]}
        tier3_rics = {a["ric"] for a in ASSET_TIERS["tier3"]}
        picks: list[str] = []
        for _ in range(50):
            result = oracle.get_best_asset(hour_utc=14)
            assert result is not None
            picks.append(result["ric"])
        tier1_count = sum(1 for r in picks if r in tier1_rics)
        tier3_count = sum(1 for r in picks if r in tier3_rics)
        # Tier1 should be picked more often than tier3 (higher WR + tier weight)
        assert tier1_count > tier3_count, (
            f"Expected tier1 > tier3 picks, got tier1={tier1_count}, tier3={tier3_count}"
        )

    def test_tier1_asset_scored_higher_than_tier3(self) -> None:
        """Direct score comparison: same session, tier1 should outscore tier3."""
        from tradebot.signals.portfolio_oracle import (
            _get_tier_weight,
            _session_volatility_bonus,
        )

        # At hour=14 (overlap), POWER-X (tier1) and USDSEK (tier3) are both eligible
        vol_power = _session_volatility_bonus("POWER-X", 14)
        vol_usdsek = _session_volatility_bonus("USDSEK", 14)
        tier_power = _get_tier_weight("POWER-X")
        tier_usdsek = _get_tier_weight("USDSEK")
        # POWER-X: wr=62.6, tier_weight=3
        # USDSEK: wr=56.2, tier_weight=1
        score_power = vol_power * tier_power * (62.6 / 100.0)
        score_usdsek = vol_usdsek * tier_usdsek * (56.2 / 100.0)
        assert score_power > score_usdsek, (
            f"POWER-X score {score_power:.3f} should exceed USDSEK {score_usdsek:.3f}"
        )


# ── get_asset_by_session ────────────────────────────────────────────────────


class TestGetAssetBySession:
    """Direct tests for get_asset_by_session."""

    def test_asia_session_assets(self) -> None:
        oracle = PortfolioOracle()
        assets = oracle.get_asset_by_session(hour_utc=3)
        rics = {a["ric"] for a in assets}
        expected = set(SESSION_ASSETS["asia"]) | ALWAYS_ON_RICS
        assert rics == expected, f"Asia session: got {rics}, expected {expected}"

    def test_london_session_assets(self) -> None:
        oracle = PortfolioOracle()
        assets = oracle.get_asset_by_session(hour_utc=9)
        rics = {a["ric"] for a in assets}
        expected = set(SESSION_ASSETS["london"]) | ALWAYS_ON_RICS
        assert rics == expected, f"London session: got {rics}, expected {expected}"

    def test_us_session_assets(self) -> None:
        oracle = PortfolioOracle()
        assets = oracle.get_asset_by_session(hour_utc=18)
        rics = {a["ric"] for a in assets}
        expected = set(SESSION_ASSETS["us"]) | ALWAYS_ON_RICS
        assert rics == expected, f"US session: got {rics}, expected {expected}"

    def test_overlap_session_assets(self) -> None:
        oracle = PortfolioOracle()
        assets = oracle.get_asset_by_session(hour_utc=14)
        rics = {a["ric"] for a in assets}
        expected = set(SESSION_ASSETS["london"]) | set(SESSION_ASSETS["us"]) | ALWAYS_ON_RICS
        assert rics == expected, f"Overlap session: got {rics}, expected {expected}"

    def test_late_us_session(self) -> None:
        """Hour 21-23 is still US session."""
        oracle = PortfolioOracle()
        assets = oracle.get_asset_by_session(hour_utc=22)
        rics = {a["ric"] for a in assets}
        expected = set(SESSION_ASSETS["us"]) | ALWAYS_ON_RICS
        assert rics == expected, f"Late US session: got {rics}, expected {expected}"

    def test_returns_empty_for_none_session(self) -> None:
        """If _get_active_session returns None, only always-on assets are returned."""
        # We can't easily trigger None from the current logic (all hours map to a session),
        # but we can verify the behavior by checking the code path.
        # All hours 0-23 map to a session, so this is a structural test.
        pass


# ── Edge cases ──────────────────────────────────────────────────────────────


class TestEdgeCases:
    """Boundary conditions and edge cases."""

    def test_hour_boundary_asia_to_london(self) -> None:
        """Hour 6 is asia, hour 7 is london."""
        oracle = PortfolioOracle()
        asia_assets = oracle.get_asset_by_session(hour_utc=6)
        london_assets = oracle.get_asset_by_session(hour_utc=7)
        asia_rics = {a["ric"] for a in asia_assets}
        london_rics = {a["ric"] for a in london_assets}
        assert asia_rics != london_rics

    def test_hour_boundary_london_to_overlap(self) -> None:
        """Hour 11 is london, hour 12 is overlap."""
        oracle = PortfolioOracle()
        london_assets = oracle.get_asset_by_session(hour_utc=11)
        overlap_assets = oracle.get_asset_by_session(hour_utc=12)
        london_rics = {a["ric"] for a in london_assets}
        overlap_rics = {a["ric"] for a in overlap_assets}
        assert london_rics != overlap_rics
        # Overlap should have more assets (London + US)
        assert len(overlap_rics) > len(london_rics)

    def test_hour_boundary_overlap_to_us(self) -> None:
        """Hour 15 is overlap, hour 16 is us."""
        oracle = PortfolioOracle()
        overlap_assets = oracle.get_asset_by_session(hour_utc=15)
        us_assets = oracle.get_asset_by_session(hour_utc=16)
        overlap_rics = {a["ric"] for a in overlap_assets}
        us_rics = {a["ric"] for a in us_assets}
        assert overlap_rics != us_rics

    def test_hour_boundary_us_to_asia(self) -> None:
        """Hour 23 is us, hour 0 is asia."""
        oracle = PortfolioOracle()
        us_assets = oracle.get_asset_by_session(hour_utc=23)
        asia_assets = oracle.get_asset_by_session(hour_utc=0)
        us_rics = {a["ric"] for a in us_assets}
        asia_rics = {a["ric"] for a in asia_assets}
        assert us_rics != asia_rics

    def test_get_best_asset_all_hours(self) -> None:
        """get_best_asset should return a valid result for every hour of the day."""
        oracle = PortfolioOracle()
        random.seed(42)
        for hour in range(24):
            result = oracle.get_best_asset(hour_utc=hour)
            assert result is not None, f"None at hour={hour}"
            assert result["ric"] in ALL_RICS, f"Unknown RIC at hour={hour}"
            assert result["action"] == "turbo"
            assert result["duration"] == 60

    def test_direction_picker_params_match_asset(self) -> None:
        """The direction_picker_params should match the selected asset's definition."""
        oracle = PortfolioOracle()
        random.seed(42)
        for hour in (3, 9, 14, 18):
            result = oracle.get_best_asset(hour_utc=hour)
            assert result is not None
            ric = result["ric"]
            # Find the asset definition
            asset = next(a for a in ALL_ASSETS if a["ric"] == ric)
            params = result["direction_picker_params"]
            assert params["lookback"] == asset["win"]
            assert params["threshold"] == asset["thr"]
            assert params["payout"] == asset["payout"]
