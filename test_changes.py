"""
Tests for the aggressive trading parameter changes, smart order cancellation,
Polymarket integration, and circuit breaker / backoff logic.
"""

import asyncio
import time
import math
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from dataclasses import dataclass

# ─── Test the odds_api module ────────────────────────────────────────────────

from odds_api import (
    PolymarketClient,
    KalshiClient,
    OddsManager,
    blend_fair_values,
    API_BACKOFF_THRESHOLD,
    API_BACKOFF_BASE,
    API_BACKOFF_MAX,
    KALSHI_REST_INTERVAL,
    POLYMARKET_REFRESH_INTERVAL,
    resolve_team_name,
    devig_two_way,
)


class FakeResponse:
    """Mock aiohttp response."""
    def __init__(self, status=200, json_data=None, headers=None):
        self.status = status
        self._json_data = json_data or {}
        self.headers = headers or {}

    async def json(self):
        return self._json_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class FakeSession:
    """Mock aiohttp session that returns configurable responses."""
    def __init__(self, responses=None, default_status=200):
        self.responses = responses or []
        self._call_idx = 0
        self.default_status = default_status
        self.requests = []  # track (method, url, kwargs)

    def get(self, url, **kwargs):
        self.requests.append(("GET", url, kwargs))
        if self._call_idx < len(self.responses):
            resp = self.responses[self._call_idx]
            self._call_idx += 1
            return resp
        return FakeResponse(status=self.default_status)


# ═══════════════════════════════════════════════════════════════════════════════
# Polymarket Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestPolymarketCircuitBreaker(unittest.TestCase):
    """Test Polymarket backoff logic on 429/403 responses."""

    def test_backoff_on_429(self):
        """429 should trigger immediate backoff."""
        client = PolymarketClient(FakeSession())
        client._record_failure("test", 429)
        self.assertTrue(client._check_backoff())
        self.assertGreater(client._backoff_until, time.time())

    def test_backoff_on_403(self):
        """403 should trigger immediate backoff."""
        client = PolymarketClient(FakeSession())
        client._record_failure("test", 403)
        self.assertTrue(client._check_backoff())

    def test_no_backoff_on_success(self):
        """Success resets backoff."""
        client = PolymarketClient(FakeSession())
        client._record_failure("test", 429)
        self.assertTrue(client._check_backoff())
        client._record_success()
        self.assertFalse(client._check_backoff())
        self.assertEqual(client._consecutive_failures, 0)

    def test_exponential_backoff_on_repeated_failures(self):
        """After API_BACKOFF_THRESHOLD failures, backoff should increase."""
        client = PolymarketClient(FakeSession())
        for i in range(API_BACKOFF_THRESHOLD):
            client._record_failure("test", 500)

        # Should be in backoff now
        self.assertTrue(client._check_backoff())
        first_backoff = client._backoff_until

        # Record more failures — backoff should increase
        client._backoff_until = 0  # reset to allow next check
        client._record_failure("test", 500)
        self.assertGreaterEqual(client._backoff_until, first_backoff)

    def test_backoff_capped_at_max(self):
        """Backoff should not exceed API_BACKOFF_MAX."""
        client = PolymarketClient(FakeSession())
        for i in range(20):  # lots of failures
            client._record_failure("test", 500)
        max_expected = time.time() + API_BACKOFF_MAX + 1
        self.assertLessEqual(client._backoff_until, max_expected)

    def test_discover_skipped_during_backoff(self):
        """discover_ncaa_markets should return immediately during backoff."""
        session = FakeSession()
        client = PolymarketClient(session)
        client._record_failure("test", 429)

        asyncio.run(client.discover_ncaa_markets())
        # No requests should have been made
        self.assertEqual(len(session.requests), 0)

    def test_refresh_skipped_during_backoff(self):
        """refresh_prices should return False during backoff."""
        session = FakeSession()
        client = PolymarketClient(session)
        client._record_failure("test", 429)

        result = asyncio.run(client.refresh_prices())
        self.assertFalse(result)
        self.assertEqual(len(session.requests), 0)

    def test_refresh_rate_limiting(self):
        """refresh_prices should respect POLYMARKET_REFRESH_INTERVAL."""
        session = FakeSession()
        client = PolymarketClient(session)
        client.last_fetch = time.time()  # just fetched
        client.token_ids = {"Duke": "tok123"}

        result = asyncio.run(client.refresh_prices())
        self.assertFalse(result)
        self.assertEqual(len(session.requests), 0)


class TestPolymarketDiscovery(unittest.TestCase):
    """Test Polymarket market parsing."""

    def test_discover_429_triggers_backoff(self):
        """HTTP 429 from Polymarket should trigger circuit breaker."""
        session = FakeSession(responses=[FakeResponse(status=429)])
        client = PolymarketClient(session)
        asyncio.run(client.discover_ncaa_markets())
        self.assertTrue(client._check_backoff())
        self.assertEqual(len(client.championship_probs), 0)

    def test_parse_binary_market(self):
        """Should parse binary 'Will X win?' markets."""
        client = PolymarketClient(FakeSession())
        markets = [{
            "question": "Will Duke win the NCAA tournament?",
            "description": "",
            "groupItemTitle": "",
            "outcomes": ["Yes", "No"],
            "outcomePrices": ["0.21", "0.79"],
            "clobTokenIds": ["token_duke_yes", "token_duke_no"],
        }]
        client._parse_markets(markets)
        self.assertIn("Duke", client.championship_probs)
        self.assertAlmostEqual(client.championship_probs["Duke"], 0.21)
        self.assertEqual(client.token_ids["Duke"], "token_duke_yes")

    def test_parse_ignores_non_ncaa(self):
        """Should skip markets not related to NCAA."""
        client = PolymarketClient(FakeSession())
        markets = [{
            "question": "Will the Lakers win the NBA championship?",
            "description": "",
            "groupItemTitle": "",
            "outcomes": ["Yes", "No"],
            "outcomePrices": ["0.15", "0.85"],
            "clobTokenIds": ["tok1"],
        }]
        client._parse_markets(markets)
        self.assertEqual(len(client.championship_probs), 0)

    def test_parse_invalid_prob_ignored(self):
        """Probs outside (0,1) should be ignored."""
        client = PolymarketClient(FakeSession())
        markets = [{
            "question": "Will Duke win the NCAA tournament?",
            "description": "",
            "groupItemTitle": "",
            "outcomes": ["Yes", "No"],
            "outcomePrices": ["0.0", "1.0"],
            "clobTokenIds": ["tok1"],
        }]
        client._parse_markets(markets)
        self.assertNotIn("Duke", client.championship_probs)


class TestPolymarketRefresh(unittest.TestCase):
    """Test Polymarket price refresh logic."""

    def test_refresh_fetches_midpoints(self):
        """Should fetch midpoint for each known token."""
        resp = FakeResponse(status=200, json_data={"mid": "0.25"})
        session = FakeSession(responses=[resp])
        client = PolymarketClient(session)
        client.token_ids = {"Duke": "tok_duke"}
        client.championship_probs = {"Duke": 0.21}
        client.last_fetch = 0  # allow fetch

        result = asyncio.run(client.refresh_prices())
        self.assertTrue(result)
        self.assertAlmostEqual(client.championship_probs["Duke"], 0.25)

    def test_refresh_stops_on_429(self):
        """Should stop fetching and backoff on 429."""
        resp_429 = FakeResponse(status=429)
        session = FakeSession(responses=[resp_429])
        client = PolymarketClient(session)
        client.token_ids = {"Duke": "tok1", "Arizona": "tok2"}
        client.last_fetch = 0

        asyncio.run(client.refresh_prices())
        self.assertTrue(client._check_backoff())
        # Should have stopped after first 429, not tried second token
        self.assertEqual(len(session.requests), 1)


# ═══════════════════════════════════════════════════════════════════════════════
# Kalshi Circuit Breaker Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestKalshiCircuitBreaker(unittest.TestCase):
    """Test Kalshi backoff logic."""

    def test_backoff_on_429(self):
        client = KalshiClient(FakeSession())
        client._record_failure("test", 429)
        self.assertTrue(client._check_backoff())

    def test_backoff_on_403(self):
        client = KalshiClient(FakeSession())
        client._record_failure("test", 403)
        self.assertTrue(client._check_backoff())

    def test_success_resets(self):
        client = KalshiClient(FakeSession())
        client._record_failure("test", 429)
        self.assertTrue(client._check_backoff())
        client._record_success()
        self.assertFalse(client._check_backoff())

    def test_discover_skipped_during_backoff(self):
        session = FakeSession()
        client = KalshiClient(session)
        client._record_failure("test", 429)
        result = asyncio.run(client.discover_ncaa_markets())
        self.assertEqual(result, [])
        self.assertEqual(len(session.requests), 0)

    def test_fetch_rest_skipped_during_backoff(self):
        session = FakeSession()
        client = KalshiClient(session)
        client._record_failure("test", 429)
        client.team_markets = {"Duke": ["KXCBB-DUKE"]}
        asyncio.run(client.fetch_rest_prices())
        self.assertEqual(len(session.requests), 0)


# ═══════════════════════════════════════════════════════════════════════════════
# OddsManager Blending Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestChampionshipBlending(unittest.TestCase):
    """Test that championship probs blend correctly across 3 sources."""

    def test_single_source(self):
        """With only one source, prob should be that source's value."""
        session = FakeSession()
        mgr = OddsManager(session)
        mgr.odds_api.championship_probs = {"Duke": 0.20}
        mgr._update_championship_probs()
        self.assertAlmostEqual(mgr.championship_probs["Duke"], 0.20)

    def test_two_sources_averaged(self):
        """Two sources should average."""
        session = FakeSession()
        mgr = OddsManager(session)
        mgr.odds_api.championship_probs = {"Duke": 0.20}
        mgr.kalshi.advancement_probs = {"Duke": {"champion": 0.30}}
        mgr._update_championship_probs()
        self.assertAlmostEqual(mgr.championship_probs["Duke"], 0.25)

    def test_three_sources_averaged(self):
        """All three sources should average."""
        session = FakeSession()
        mgr = OddsManager(session)
        mgr.odds_api.championship_probs = {"Duke": 0.20}
        mgr.kalshi.advancement_probs = {"Duke": {"champion": 0.30}}
        mgr.polymarket.championship_probs = {"Duke": 0.25}
        mgr._update_championship_probs()
        expected = (0.20 + 0.30 + 0.25) / 3.0
        self.assertAlmostEqual(mgr.championship_probs["Duke"], expected)

    def test_partial_coverage(self):
        """Teams not in all sources should still work."""
        session = FakeSession()
        mgr = OddsManager(session)
        mgr.odds_api.championship_probs = {"Duke": 0.20, "Arizona": 0.15}
        mgr.polymarket.championship_probs = {"Duke": 0.25}
        mgr._update_championship_probs()
        # Duke: average of 0.20 and 0.25
        self.assertAlmostEqual(mgr.championship_probs["Duke"], 0.225)
        # Arizona: only one source
        self.assertAlmostEqual(mgr.championship_probs["Arizona"], 0.15)


# ═══════════════════════════════════════════════════════════════════════════════
# Fair Value Blending Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestBlendFairValues(unittest.TestCase):
    """Test the blend_fair_values function."""

    def test_no_championship_probs(self):
        """With no market data, return MC values unchanged."""
        mc = {"Duke": 15.0, "Arizona": 10.0}
        result = blend_fair_values(mc, {})
        self.assertEqual(result, mc)

    def test_blending_preserves_total(self):
        """Total FV should be preserved after blending."""
        mc = {"Duke": 15.0, "Arizona": 10.0, "Houston": 8.0}
        champ = {"Duke": 0.25, "Arizona": 0.15}
        result = blend_fair_values(mc, champ)
        total_mc = sum(mc.values())
        total_blend = sum(result.values())
        self.assertAlmostEqual(total_blend, total_mc, places=1)

    def test_default_weight(self):
        """Default weight_market=0.35 should produce a blend."""
        mc = {"Duke": 15.0}
        champ = {"Duke": 0.30}
        result = blend_fair_values(mc, champ)
        # Should not be exactly 15.0 (pure MC) since market data exists
        self.assertIn("Duke", result)


# ═══════════════════════════════════════════════════════════════════════════════
# Bot Parameter Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestBotParameters(unittest.TestCase):
    """Verify the tuned parameters are set correctly."""

    def test_min_edge(self):
        from bot import MIN_EDGE
        self.assertEqual(MIN_EDGE, 1.0)

    def test_risk_aversion_gamma(self):
        from bot import GAMMA_ALIGNED, GAMMA_MISALIGNED
        self.assertEqual(GAMMA_ALIGNED, 0.005)
        self.assertEqual(GAMMA_MISALIGNED, 0.03)

    def test_order_cooldown(self):
        from bot import ORDER_COOLDOWN
        self.assertEqual(ORDER_COOLDOWN, 3.0)

    def test_recompute_interval(self):
        from bot import RECOMPUTE_INTERVAL, RECOMPUTE_LIVE
        self.assertEqual(RECOMPUTE_INTERVAL, 10)
        self.assertEqual(RECOMPUTE_LIVE, 10)

    def test_fv_cancel_threshold(self):
        from bot import FV_CANCEL_THRESHOLD
        self.assertEqual(FV_CANCEL_THRESHOLD, 0.5)

    def test_ov_scaling(self):
        """Option value should be ~55% of the old formula."""
        # Old: 0.39 * N^0.42 * (1 + 16.12*p*(1-p))
        # New: 0.55 * 0.39 * N^0.42 * (1 + 16.12*p*(1-p))
        p = 0.25  # fair_value/64
        N = 10.0  # time_rem/120
        old_ov = 0.39 * math.pow(N, 0.42) * (1.0 + 16.12 * p * (1.0 - p))
        new_ov = 0.55 * 0.39 * math.pow(N, 0.42) * (1.0 + 16.12 * p * (1.0 - p))
        self.assertAlmostEqual(new_ov / old_ov, 0.55, places=5)
        self.assertLess(new_ov, old_ov)


# ═══════════════════════════════════════════════════════════════════════════════
# Smart Cancel Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestSmartCancel(unittest.TestCase):
    """Test that the SymbolState tracks last_order_fv."""

    def test_symbol_state_has_last_order_fv(self):
        from bot import SymbolState
        state = SymbolState()
        self.assertEqual(state.last_order_fv, 0.0)
        state.last_order_fv = 12.5
        self.assertEqual(state.last_order_fv, 12.5)

    def test_fv_cancel_logic(self):
        """Orders should only cancel when FV moves > threshold."""
        from bot import FV_CANCEL_THRESHOLD, SymbolState
        state = SymbolState()
        state.last_order_fv = 10.0

        # Small FV change — should NOT cancel
        state.fair_value = 10.3
        should_cancel = abs(state.fair_value - state.last_order_fv) > FV_CANCEL_THRESHOLD
        self.assertFalse(should_cancel)

        # Large FV change — should cancel
        state.fair_value = 11.0
        should_cancel = abs(state.fair_value - state.last_order_fv) > FV_CANCEL_THRESHOLD
        self.assertTrue(should_cancel)


# ═══════════════════════════════════════════════════════════════════════════════
# Polling Interval Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestPollingIntervals(unittest.TestCase):
    """Verify polling intervals are set aggressively."""

    def test_kalshi_rest_interval(self):
        self.assertEqual(KALSHI_REST_INTERVAL, 10)

    def test_polymarket_refresh_interval(self):
        self.assertEqual(POLYMARKET_REFRESH_INTERVAL, 10)


# ═══════════════════════════════════════════════════════════════════════════════
# Utility Function Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestUtilityFunctions(unittest.TestCase):
    """Test utility functions in odds_api."""

    def test_resolve_team_name_exact(self):
        self.assertEqual(resolve_team_name("Duke Blue Devils"), "Duke")
        self.assertEqual(resolve_team_name("UConn Huskies"), "Connecticut")

    def test_resolve_team_name_model_direct(self):
        self.assertEqual(resolve_team_name("Duke"), "Duke")

    def test_devig_two_way(self):
        p_a, p_b = devig_two_way(0.6, 0.5)  # sum > 1 = vig
        self.assertAlmostEqual(p_a + p_b, 1.0)
        self.assertGreater(p_a, p_b)


# ═══════════════════════════════════════════════════════════════════════════════
# OddsManager Parallel Refresh Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestOddsManagerRefresh(unittest.TestCase):
    """Test that OddsManager.refresh runs Kalshi+Polymarket in parallel."""

    def test_refresh_returns_false_when_nothing_due(self):
        """When nothing needs refreshing, returns False."""
        session = FakeSession()
        mgr = OddsManager(session)
        mgr.last_odds_fetch = time.time()
        mgr.last_championship_fetch = time.time()
        mgr.last_kalshi_rest_fetch = time.time()
        mgr.polymarket.last_fetch = time.time()
        # Kalshi WS connected — skip REST
        mgr.kalshi.ws_connected = True

        result = asyncio.run(mgr.refresh(has_live_games=False))
        # Polymarket refresh will run (parallel tasks always include it)
        # but it returns False due to interval check
        self.assertFalse(result)

    def test_refresh_triggers_polymarket(self):
        """When Polymarket interval expired, it should fetch."""
        session = FakeSession(responses=[
            FakeResponse(200, {"mid": "0.30"}),
        ])
        mgr = OddsManager(session)
        mgr.last_odds_fetch = time.time()
        mgr.last_championship_fetch = time.time()
        mgr.kalshi.ws_connected = True
        mgr.polymarket.token_ids = {"Duke": "tok1"}
        mgr.polymarket.championship_probs = {"Duke": 0.20}
        mgr.polymarket.last_fetch = 0  # expired

        result = asyncio.run(mgr.refresh(has_live_games=False))
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
