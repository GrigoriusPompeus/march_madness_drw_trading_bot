"""
External Odds Integration for NCAA Tournament Bot
Fetches live odds from The Odds API (bookmaker consensus) and Kalshi (prediction market)
to replace/augment hardcoded team ratings with fresh market data.

Credit budget strategy (The Odds API - 500 free credits/month):
  - 1 credit = 1 API call with 1 market + 1 region (regardless of # games returned)
  - Poll every ~15 min during live games, less often pre-game
  - Use Kalshi WebSocket as primary real-time feed (free, unlimited)

Sources:
  1. The Odds API: consensus moneylines/spreads from 15+ US bookmakers
  2. Kalshi: prediction market prices = implied tournament advancement probabilities
"""

import asyncio
import json
import logging
import math
import os
import time
from typing import Dict, List, Optional, Set, Tuple

import aiohttp
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("odds")

# ── The Odds API config ──────────────────────────────────────────────────────
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "YOUR_ODDS_API_KEY_HERE")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
NCAAB_SPORT = "basketball_ncaab"

# ── Kalshi config ────────────────────────────────────────────────────────────
KALSHI_API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"

# ── Polling intervals ────────────────────────────────────────────────────────
ODDS_API_PREGAME_INTERVAL = 900     # 15 min pre-game
ODDS_API_LIVE_INTERVAL = 600       # 10 min during live games
KALSHI_REST_INTERVAL = 120         # 2 min REST fallback if WS fails
CHAMPIONSHIP_ODDS_INTERVAL = 3600  # 1 hour for futures


# ═══════════════════════════════════════════════════════════════════════════════
# Utility functions
# ═══════════════════════════════════════════════════════════════════════════════

def american_to_implied_prob(american_odds: int) -> float:
    """Convert American odds to implied probability (includes vig)."""
    if american_odds > 0:
        return 100.0 / (american_odds + 100.0)
    else:
        return abs(american_odds) / (abs(american_odds) + 100.0)


def decimal_to_implied_prob(decimal_odds: float) -> float:
    """Convert decimal odds to implied probability."""
    if decimal_odds <= 0:
        return 0.0
    return 1.0 / decimal_odds


def devig_two_way(prob_a: float, prob_b: float) -> Tuple[float, float]:
    """
    Remove bookmaker vig from a two-way market using multiplicative method.
    Input: raw implied probabilities (sum > 1.0 due to vig).
    Output: true probabilities (sum = 1.0).
    """
    total = prob_a + prob_b
    if total <= 0:
        return 0.5, 0.5
    return prob_a / total, prob_b / total


def devig_multi_way(probs: List[float]) -> List[float]:
    """Remove vig from a multi-way market (e.g., championship futures)."""
    total = sum(probs)
    if total <= 0:
        n = len(probs)
        return [1.0 / n] * n
    return [p / total for p in probs]


def prob_to_adjem_diff(prob: float) -> float:
    """
    Convert a win probability to an AdjEM rating difference.
    Inverse of: prob = 1 / (1 + 10^(-diff/11))
    """
    if prob <= 0.001:
        return -40.0
    if prob >= 0.999:
        return 40.0
    return -11.0 * math.log10(1.0 / prob - 1.0)


# ═══════════════════════════════════════════════════════════════════════════════
# The Odds API name -> model name mapping
# ═══════════════════════════════════════════════════════════════════════════════

ODDS_API_TO_MODEL = {
    "Duke Blue Devils": "Duke",
    "Arizona Wildcats": "Arizona",
    "Michigan Wolverines": "Michigan",
    "Florida Gators": "Florida",
    "Houston Cougars": "Houston",
    "Iowa State Cyclones": "Iowa State",
    "Illinois Fighting Illini": "Illinois",
    "Purdue Boilermakers": "Purdue",
    "Michigan State Spartans": "Michigan State",
    "Gonzaga Bulldogs": "Gonzaga",
    "UConn Huskies": "Connecticut",
    "Connecticut Huskies": "Connecticut",
    "Vanderbilt Commodores": "Vanderbilt",
    "Virginia Cavaliers": "Virginia",
    "Nebraska Cornhuskers": "Nebraska",
    "Kansas Jayhawks": "Kansas",
    "Tennessee Volunteers": "Tennessee",
    "Alabama Crimson Tide": "Alabama",
    "Arkansas Razorbacks": "Arkansas",
    "Louisville Cardinals": "Louisville",
    "Texas Tech Red Raiders": "Texas Tech",
    "St. John's Red Storm": "St. John's",
    "North Carolina Tar Heels": "North Carolina",
    "Wisconsin Badgers": "Wisconsin",
    "Saint Mary's Gaels": "Saint Mary's",
    "UCLA Bruins": "UCLA",
    "BYU Cougars": "BYU",
    "Brigham Young Cougars": "BYU",
    "Clemson Tigers": "Clemson",
    "Kentucky Wildcats": "Kentucky",
    "Villanova Wildcats": "Villanova",
    "Ohio State Buckeyes": "Ohio State",
    "Georgia Bulldogs": "Georgia",
    "Miami Hurricanes": "Miami FL",
    "Iowa Hawkeyes": "Iowa",
    "Missouri Tigers": "Missouri",
    "TCU Horned Frogs": "TCU",
    "Texas A&M Aggies": "Texas A&M",
    "UCF Knights": "UCF",
    "VCU Rams": "VCU",
    "Santa Clara Broncos": "Santa Clara",
    "Utah State Aggies": "Utah State",
    "South Florida Bulls": "South Florida",
    "Saint Louis Billikens": "Saint Louis",
    "SMU Mustangs": "SMU",
    "NC State Wolfpack": "NC State",
    "Texas Longhorns": "Texas",
    "Northern Iowa Panthers": "Northern Iowa",
    "Akron Zips": "Akron",
    "McNeese Cowboys": "McNeese",
    "High Point Panthers": "High Point",
    "Hofstra Pride": "Hofstra",
    "Troy Trojans": "Troy",
    "Hawaii Rainbow Warriors": "Hawaii",
    "Hawai'i Rainbow Warriors": "Hawaii",
    "Miami (OH) RedHawks": "Miami OH",
    "Miami OH RedHawks": "Miami OH",
    "Penn Quakers": "Penn",
    "Pennsylvania Quakers": "Penn",
    "Kennesaw State Owls": "Kennesaw State",
    "North Dakota State Bison": "North Dakota State",
    "Cal Baptist Lancers": "Cal Baptist",
    "California Baptist Lancers": "Cal Baptist",
    "Wright State Raiders": "Wright State",
    "Furman Paladins": "Furman",
    "Queens Royals": "Queens",
    "Idaho Vandals": "Idaho",
    "Tennessee State Tigers": "Tennessee State",
    "Siena Saints": "Siena",
    "LIU Sharks": "Long Island",
    "Long Island University Sharks": "Long Island",
    "Lehigh Mountain Hawks": "Lehigh",
    "Prairie View A&M Panthers": "Prairie View A&M",
    "Howard Bison": "Howard",
    "UMBC Retrievers": "UMBC",
}


def resolve_team_name(api_name: str) -> Optional[str]:
    """Resolve an Odds API / Kalshi team name to our model name."""
    # Exact match
    if api_name in ODDS_API_TO_MODEL:
        return ODDS_API_TO_MODEL[api_name]
    # Partial match - only check if a known key appears IN the input (not vice versa)
    # to avoid "Michigan" matching "Michigan State Spartans".
    # If the input is shorter than all keys, step 3 (TEAM_RATINGS regex) handles it.
    import re
    for api, model in sorted(ODDS_API_TO_MODEL.items(), key=lambda x: len(x[0]), reverse=True):
        if re.search(r'\b' + re.escape(api) + r'\b', api_name):
            return model
    # Try matching against model names directly - sort by length descending so
    # "Michigan State" is checked before "Michigan", etc.
    import re
    from model import TEAM_RATINGS
    for team in sorted(TEAM_RATINGS.keys(), key=len, reverse=True):
        if team.lower() == api_name.lower():
            return team
        if re.search(r'\b' + re.escape(team.lower()) + r'\b', api_name.lower()):
            return team
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# The Odds API Client
# ═══════════════════════════════════════════════════════════════════════════════

class TheOddsAPIClient:
    """Client for The Odds API (the-odds-api.com)."""

    def __init__(self, session: aiohttp.ClientSession, api_key: str = ODDS_API_KEY):
        self.session = session
        self.api_key = api_key
        self.credits_remaining: Optional[int] = None
        self.credits_used: Optional[int] = None
        self.last_fetch_time: float = 0.0
        # Cached data
        self.matchup_probs: Dict[Tuple[str, str], float] = {}   # (teamA, teamB) -> P(A wins)
        self.championship_probs: Dict[str, float] = {}           # team -> P(champion)

    def _update_credits(self, headers: dict) -> None:
        """Track credit usage from response headers."""
        remaining = headers.get("x-requests-remaining")
        used = headers.get("x-requests-used")
        last_cost = headers.get("x-requests-last")
        if remaining is not None:
            self.credits_remaining = int(remaining)
        if used is not None:
            self.credits_used = int(used)
        if last_cost is not None:
            log.info(f"Odds API: cost={last_cost} credits, remaining={self.credits_remaining}, used={self.credits_used}")

    async def check_credits(self) -> Optional[int]:
        """Check remaining credits without using any (free /sports call)."""
        try:
            url = f"{ODDS_API_BASE}/sports"
            params = {"apiKey": self.api_key}
            async with self.session.get(url, params=params) as resp:
                self._update_credits(dict(resp.headers))
                return self.credits_remaining
        except Exception as e:
            log.warning(f"Failed to check Odds API credits: {e}")
            return None

    async def fetch_game_odds(self, event_id: Optional[str] = None) -> List[Dict]:
        """
        Fetch moneyline odds for NCAA basketball games.
        Cost: 1 credit (1 market × 1 region).
        Returns all upcoming + live games unless event_id is specified.
        """
        if event_id:
            url = f"{ODDS_API_BASE}/sports/{NCAAB_SPORT}/events/{event_id}/odds"
        else:
            url = f"{ODDS_API_BASE}/sports/{NCAAB_SPORT}/odds"

        params = {
            "apiKey": self.api_key,
            "regions": "us",
            "markets": "h2h",
            "oddsFormat": "american",
        }

        try:
            async with self.session.get(url, params=params) as resp:
                self._update_credits(dict(resp.headers))
                if resp.status == 429:
                    log.warning("Odds API: rate limited (429). Credits exhausted.")
                    return []
                if resp.status == 422:
                    log.warning("Odds API: sport not in season or no events (422).")
                    return []
                if resp.status != 200:
                    log.warning(f"Odds API returned status {resp.status}")
                    return []
                data = await resp.json()
                self.last_fetch_time = time.time()
                return data
        except Exception as e:
            log.error(f"Odds API fetch failed: {e}")
            return []

    async def fetch_championship_odds(self) -> List[Dict]:
        """
        Fetch championship/outright winner odds.
        Cost: 1 credit. Returns futures odds for tournament winner.
        """
        url = f"{ODDS_API_BASE}/sports/{NCAAB_SPORT}_championship_winner/odds"
        params = {
            "apiKey": self.api_key,
            "regions": "us",
            "markets": "outrights",
            "oddsFormat": "american",
        }

        try:
            async with self.session.get(url, params=params) as resp:
                self._update_credits(dict(resp.headers))
                if resp.status == 429:
                    log.warning("Odds API: rate limited for championship odds.")
                    return []
                if resp.status == 422:
                    log.info("Odds API: championship market not available (422).")
                    return []
                if resp.status != 200:
                    log.warning(f"Odds API championship returned status {resp.status}")
                    return []
                data = await resp.json()
                return data
        except Exception as e:
            log.error(f"Odds API championship fetch failed: {e}")
            return []

    def parse_game_odds(self, events: List[Dict]) -> Dict[Tuple[str, str], float]:
        """
        Parse game odds into matchup probabilities.
        Returns dict of (home_model_name, away_model_name) -> P(home wins).
        Averages across bookmakers and devigs.
        """
        matchups = {}

        for event in events:
            bookmakers = event.get("bookmakers", [])
            if not bookmakers:
                continue

            home_team_api = event.get("home_team", "")
            away_team_api = event.get("away_team", "")
            home = resolve_team_name(home_team_api)
            away = resolve_team_name(away_team_api)
            if not home or not away:
                log.debug(f"Could not resolve: {home_team_api} vs {away_team_api}")
                continue

            # Collect devigged probabilities from each bookmaker
            home_probs = []
            for bk in bookmakers:
                for market in bk.get("markets", []):
                    if market.get("key") != "h2h":
                        continue
                    outcomes = {o["name"]: o["price"] for o in market.get("outcomes", [])}
                    home_odds = outcomes.get(home_team_api)
                    away_odds = outcomes.get(away_team_api)
                    if home_odds is None or away_odds is None:
                        continue
                    h_imp = american_to_implied_prob(home_odds)
                    a_imp = american_to_implied_prob(away_odds)
                    h_true, _ = devig_two_way(h_imp, a_imp)
                    home_probs.append(h_true)

            if home_probs:
                avg_prob = sum(home_probs) / len(home_probs)
                matchups[(home, away)] = avg_prob
                # Also store reverse
                matchups[(away, home)] = 1.0 - avg_prob
                log.info(
                    f"Odds: {home} vs {away} -> P({home})={avg_prob:.1%} "
                    f"(from {len(home_probs)} bookmakers)"
                )

        self.matchup_probs.update(matchups)
        return matchups

    def parse_championship_odds(self, events: List[Dict]) -> Dict[str, float]:
        """
        Parse championship futures into team -> P(champion).
        Averages across bookmakers and devigs.
        """
        # Collect raw probabilities per team across all bookmakers
        team_probs: Dict[str, List[float]] = {}

        for event in events:
            for bk in event.get("bookmakers", []):
                for market in bk.get("markets", []):
                    if market.get("key") != "outrights":
                        continue
                    raw_probs = []
                    team_names = []
                    for outcome in market.get("outcomes", []):
                        api_name = outcome["name"]
                        odds = outcome["price"]
                        model_name = resolve_team_name(api_name)
                        if not model_name:
                            continue
                        imp = american_to_implied_prob(odds)
                        raw_probs.append(imp)
                        team_names.append(model_name)

                    if raw_probs:
                        devigged = devig_multi_way(raw_probs)
                        for name, prob in zip(team_names, devigged):
                            if name not in team_probs:
                                team_probs[name] = []
                            team_probs[name].append(prob)

        # Average across bookmakers
        result = {}
        for team, probs in team_probs.items():
            result[team] = sum(probs) / len(probs)

        self.championship_probs = result
        if result:
            top5 = sorted(result.items(), key=lambda x: x[1], reverse=True)[:5]
            log.info(f"Championship odds top 5: {[(t, f'{p:.1%}') for t, p in top5]}")
        return result


# ═══════════════════════════════════════════════════════════════════════════════
# Kalshi Client
# ═══════════════════════════════════════════════════════════════════════════════

class KalshiClient:
    """Client for Kalshi prediction market data (public endpoints only)."""

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.market_prices: Dict[str, float] = {}       # ticker -> last_price (= implied prob)
        self.team_markets: Dict[str, List[str]] = {}    # model_team -> list of tickers
        self.ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self.ws_connected: bool = False
        self._ws_task: Optional[asyncio.Task] = None
        # Parsed data
        self.game_winner_probs: Dict[str, float] = {}   # model_team -> P(win current game)
        self.advancement_probs: Dict[str, Dict[str, float]] = {}  # model_team -> {round: prob}

    async def discover_ncaa_markets(self) -> List[Dict]:
        """
        Discover NCAA tournament markets on Kalshi.
        Searches for basketball/tournament related markets.
        """
        all_markets = []
        cursor = None

        # Search terms to find NCAA tournament markets
        search_params_list = [
            {"status": "open", "limit": 200},
        ]

        try:
            # First, try to find the series ticker for NCAA tournament
            # Try common patterns
            for series_guess in ["KXCBB", "KXNCAA", "KXMM", "CBB", "NCAA", "MARCHMAD"]:
                url = f"{KALSHI_API_BASE}/markets"
                params = {"series_ticker": series_guess, "status": "open", "limit": 200}
                try:
                    async with self.session.get(url, params=params) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            markets = data.get("markets", [])
                            if markets:
                                log.info(f"Kalshi: found {len(markets)} markets under series '{series_guess}'")
                                all_markets.extend(markets)
                except Exception:
                    continue

            # If nothing found via series, try broader search via event titles
            if not all_markets:
                url = f"{KALSHI_API_BASE}/events"
                params = {"status": "open", "limit": 200}
                try:
                    async with self.session.get(url, params=params) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            events = data.get("events", [])
                            ncaa_events = [
                                e for e in events
                                if any(kw in (e.get("title", "") + e.get("category", "")).lower()
                                       for kw in ["ncaa", "march madness", "tournament", "cbb", "college basketball"])
                            ]
                            for event in ncaa_events:
                                event_ticker = event.get("event_ticker", "")
                                if event_ticker:
                                    mkt_url = f"{KALSHI_API_BASE}/markets"
                                    mkt_params = {"event_ticker": event_ticker, "status": "open", "limit": 200}
                                    async with self.session.get(mkt_url, params=mkt_params) as mkt_resp:
                                        if mkt_resp.status == 200:
                                            mkt_data = await mkt_resp.json()
                                            markets = mkt_data.get("markets", [])
                                            all_markets.extend(markets)
                                            log.info(f"Kalshi: found {len(markets)} markets for event '{event_ticker}'")
                except Exception as e:
                    log.warning(f"Kalshi event discovery failed: {e}")

        except Exception as e:
            log.error(f"Kalshi market discovery failed: {e}")

        # Parse markets and build team mapping
        self._parse_markets(all_markets)
        return all_markets

    def _parse_markets(self, markets: List[Dict]) -> None:
        """Parse market data and extract team probabilities."""
        for market in markets:
            ticker = market.get("ticker", "")
            title = market.get("title", "") + " " + market.get("subtitle", "")
            yes_bid = market.get("yes_bid_dollars")
            yes_ask = market.get("yes_ask_dollars")
            last_price = market.get("last_price_dollars")

            # Try to extract team name from title
            # Titles like "Will Duke win the NCAA Tournament?" or "Duke to reach Final Four"
            import re
            from model import TEAM_RATINGS
            for team in sorted(TEAM_RATINGS.keys(), key=len, reverse=True):
                if re.search(r'\b' + re.escape(team.lower()) + r'\b', title.lower()):
                    if team not in self.team_markets:
                        self.team_markets[team] = []
                    self.team_markets[team].append(ticker)

                    # Use mid price as probability, fall back to last_price
                    prob = None
                    if yes_bid is not None and yes_ask is not None:
                        # Fields are *_dollars (0.00-1.00 range), mid = probability
                        prob = (yes_bid + yes_ask) / 2.0
                    elif last_price is not None:
                        prob = last_price  # already in dollars = probability

                    if prob is not None:
                        self.market_prices[ticker] = prob

                        # Categorize by round
                        title_lower = title.lower()
                        if "champion" in title_lower or ("win" in title_lower and "tournament" in title_lower):
                            if team not in self.advancement_probs:
                                self.advancement_probs[team] = {}
                            self.advancement_probs[team]["champion"] = prob
                        elif "final four" in title_lower:
                            if team not in self.advancement_probs:
                                self.advancement_probs[team] = {}
                            self.advancement_probs[team]["final_four"] = prob
                        elif "elite eight" in title_lower or "elite 8" in title_lower:
                            if team not in self.advancement_probs:
                                self.advancement_probs[team] = {}
                            self.advancement_probs[team]["elite_eight"] = prob

                        log.info(f"Kalshi: {team} -> {ticker} = {prob:.1%} ({title.strip()[:60]})")
                    break

    async def start_websocket(self) -> None:
        """Connect to Kalshi WebSocket for real-time price updates."""
        if not self.team_markets:
            log.info("Kalshi: no markets discovered, skipping WebSocket")
            return

        tickers = []
        for team_tickers in self.team_markets.values():
            tickers.extend(team_tickers)

        if not tickers:
            return

        self._ws_task = asyncio.create_task(self._ws_loop(tickers))

    async def _ws_loop(self, tickers: List[str]) -> None:
        """WebSocket connection loop with auto-reconnect."""
        while True:
            try:
                log.info(f"Kalshi WS: connecting to subscribe to {len(tickers)} markets...")
                async with self.session.ws_connect(KALSHI_WS_URL) as ws:
                    self.ws = ws
                    self.ws_connected = True

                    # Subscribe to ticker channel (public, no auth)
                    subscribe_msg = {
                        "id": 1,
                        "cmd": "subscribe",
                        "params": {
                            "channels": ["ticker"],
                            "market_tickers": tickers[:100],  # Limit to avoid message size issues
                        }
                    }
                    await ws.send_json(subscribe_msg)
                    log.info("Kalshi WS: subscribed to ticker channel")

                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            self._handle_ws_message(msg.data)
                        elif msg.type == aiohttp.WSMsgType.PING:
                            await ws.pong(msg.data)
                        elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                            log.warning(f"Kalshi WS: connection closed/error")
                            break

            except asyncio.CancelledError:
                log.info("Kalshi WS: task cancelled")
                return
            except Exception as e:
                log.warning(f"Kalshi WS error: {e}")

            self.ws_connected = False
            log.info("Kalshi WS: reconnecting in 30s...")
            await asyncio.sleep(30)

    def _handle_ws_message(self, raw: str) -> None:
        """Handle incoming WebSocket message."""
        try:
            data = json.loads(raw)
            msg_type = data.get("type", "")

            if msg_type == "ticker":
                ticker = data.get("msg", {}).get("market_ticker", "")
                yes_bid = data.get("msg", {}).get("yes_bid")
                yes_ask = data.get("msg", {}).get("yes_ask")
                last_price = data.get("msg", {}).get("last_price")

                if ticker:
                    prob = None
                    if yes_bid is not None and yes_ask is not None:
                        prob = (yes_bid + yes_ask) / 200.0
                    elif last_price is not None:
                        prob = last_price / 100.0

                    if prob is not None:
                        old = self.market_prices.get(ticker)
                        self.market_prices[ticker] = prob
                        if old is not None and abs(prob - old) > 0.02:
                            log.info(f"Kalshi WS: {ticker} {old:.1%} -> {prob:.1%}")

                        # Update advancement probs - only update the specific
                        # round that this ticker corresponds to (not all rounds)
                        for team, team_tickers in self.team_markets.items():
                            if ticker in team_tickers:
                                if team in self.advancement_probs:
                                    # Find which round this ticker maps to by
                                    # checking the ticker name for round keywords
                                    ticker_lower = ticker.lower()
                                    if "champion" in ticker_lower or "winner" in ticker_lower:
                                        if "champion" in self.advancement_probs[team]:
                                            self.advancement_probs[team]["champion"] = prob
                                    elif "final" in ticker_lower or "f4" in ticker_lower:
                                        if "final_four" in self.advancement_probs[team]:
                                            self.advancement_probs[team]["final_four"] = prob
                                    elif "elite" in ticker_lower or "e8" in ticker_lower:
                                        if "elite_eight" in self.advancement_probs[team]:
                                            self.advancement_probs[team]["elite_eight"] = prob
                                break

        except Exception as e:
            log.debug(f"Kalshi WS parse error: {e}")

    async def fetch_rest_prices(self) -> None:
        """Fallback: fetch prices via REST if WebSocket is down."""
        if not self.team_markets:
            return

        all_tickers = []
        for tickers in self.team_markets.values():
            all_tickers.extend(tickers)

        if not all_tickers:
            return

        # Batch fetch (up to 200 per request)
        for i in range(0, len(all_tickers), 100):
            batch = all_tickers[i:i+100]
            url = f"{KALSHI_API_BASE}/markets"
            params = {"tickers": ",".join(batch), "limit": 200}
            try:
                async with self.session.get(url, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        self._parse_markets(data.get("markets", []))
            except Exception as e:
                log.warning(f"Kalshi REST fetch failed: {e}")

    async def stop(self) -> None:
        """Clean shutdown."""
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass


# ═══════════════════════════════════════════════════════════════════════════════
# Unified Odds Manager
# ═══════════════════════════════════════════════════════════════════════════════

class OddsManager:
    """
    Orchestrates odds fetching from The Odds API + Kalshi.
    Provides unified interface for the bot to get fresh probabilities.
    """

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.odds_api = TheOddsAPIClient(session)
        self.kalshi = KalshiClient(session)
        self.last_odds_fetch: float = 0.0
        self.last_championship_fetch: float = 0.0
        self.last_kalshi_rest_fetch: float = 0.0
        self.initialized: bool = False

        # ── Blended outputs ──────────────────────────────────────────────
        # Matchup probability overrides: (teamA, teamB) -> P(A wins)
        # Used by model.py to override rating-based win_probability
        self.matchup_overrides: Dict[Tuple[str, str], float] = {}

        # Championship probability per team (blended from bookmakers + Kalshi)
        self.championship_probs: Dict[str, float] = {}

        # Rating adjustments: team -> adjusted AdjEM rating
        # Calibrated from external odds data
        self.adjusted_ratings: Dict[str, float] = {}

    async def initialize(self) -> None:
        """
        One-time startup: fetch initial odds and connect to Kalshi WS.
        Cost: ~2 Odds API credits (game odds + championship odds).
        """
        log.info("OddsManager: initializing...")

        # Check credits first (free)
        credits = await self.odds_api.check_credits()
        if credits is not None:
            log.info(f"OddsManager: {credits} Odds API credits remaining")
            if credits < 10:
                log.warning("OddsManager: very low credits! Will minimize API calls.")

        # Fetch game odds (1 credit)
        events = await self.odds_api.fetch_game_odds()
        if events:
            self.odds_api.parse_game_odds(events)
            self._update_matchup_overrides()
            log.info(f"OddsManager: loaded {len(self.matchup_overrides)//2} matchup probabilities from bookmakers")

        # Fetch championship odds (1 credit)
        champ_events = await self.odds_api.fetch_championship_odds()
        if champ_events:
            self.odds_api.parse_championship_odds(champ_events)
            self._update_championship_probs()

        # Discover Kalshi markets and connect WebSocket
        try:
            await self.kalshi.discover_ncaa_markets()
            if self.kalshi.team_markets:
                log.info(f"OddsManager: found Kalshi markets for {len(self.kalshi.team_markets)} teams")
                await self.kalshi.start_websocket()
                self._update_championship_probs()
            else:
                log.info("OddsManager: no Kalshi NCAA markets found (may not be available)")
        except Exception as e:
            log.warning(f"OddsManager: Kalshi init failed (non-critical): {e}")

        # Calibrate ratings from collected data
        self._calibrate_ratings()

        self.last_odds_fetch = time.time()
        self.last_championship_fetch = time.time()
        self.initialized = True
        log.info("OddsManager: initialization complete")

    async def refresh(self, has_live_games: bool = False) -> bool:
        """
        Periodic refresh of odds data. Returns True if data was updated.
        Adapts polling frequency based on whether games are live.
        """
        now = time.time()
        updated = False

        # Refresh game odds from The Odds API
        interval = ODDS_API_LIVE_INTERVAL if has_live_games else ODDS_API_PREGAME_INTERVAL
        if now - self.last_odds_fetch > interval:
            # Check credits before calling
            if self.odds_api.credits_remaining is None or self.odds_api.credits_remaining > 20:
                events = await self.odds_api.fetch_game_odds()
                if events:
                    self.odds_api.parse_game_odds(events)
                    self._update_matchup_overrides()
                    self._calibrate_ratings()
                    updated = True
                self.last_odds_fetch = now
            else:
                log.warning(f"OddsManager: skipping Odds API refresh (only {self.odds_api.credits_remaining} credits left)")

        # Refresh championship odds (less frequently)
        if now - self.last_championship_fetch > CHAMPIONSHIP_ODDS_INTERVAL:
            if self.odds_api.credits_remaining is None or self.odds_api.credits_remaining > 30:
                champ_events = await self.odds_api.fetch_championship_odds()
                if champ_events:
                    self.odds_api.parse_championship_odds(champ_events)
                    self._update_championship_probs()
                    updated = True
                self.last_championship_fetch = now

        # Kalshi REST fallback (if WebSocket is down)
        if not self.kalshi.ws_connected and now - self.last_kalshi_rest_fetch > KALSHI_REST_INTERVAL:
            await self.kalshi.fetch_rest_prices()
            self._update_championship_probs()
            self.last_kalshi_rest_fetch = now
            updated = True

        return updated

    def _update_matchup_overrides(self) -> None:
        """Update matchup probability overrides from bookmaker data."""
        self.matchup_overrides = dict(self.odds_api.matchup_probs)

    def _update_championship_probs(self) -> None:
        """Blend championship probabilities from Odds API + Kalshi."""
        probs = {}

        # Start with Odds API championship data
        for team, prob in self.odds_api.championship_probs.items():
            probs[team] = prob

        # Blend in Kalshi data (if available)
        for team, rounds in self.kalshi.advancement_probs.items():
            kalshi_champ = rounds.get("champion")
            if kalshi_champ is not None:
                if team in probs:
                    # Average bookmaker and prediction market
                    probs[team] = (probs[team] + kalshi_champ) / 2.0
                else:
                    probs[team] = kalshi_champ

        self.championship_probs = probs

    def _calibrate_ratings(self) -> None:
        """
        Calibrate AdjEM ratings from external matchup odds.

        For each matchup where we have bookmaker data, compute what
        rating difference the market implies, then adjust our ratings
        to better match. Uses iterative averaging to converge.
        """
        from model import TEAM_RATINGS

        if not self.matchup_overrides:
            # No external data - keep original ratings
            self.adjusted_ratings = dict(TEAM_RATINGS)
            return

        # Start from original ratings
        ratings = dict(TEAM_RATINGS)

        # For each matchup, compute implied rating difference
        # and nudge ratings toward market consensus
        adjustments: Dict[str, List[float]] = {}

        for (team_a, team_b), market_prob in self.matchup_overrides.items():
            if team_a not in ratings or team_b not in ratings:
                continue

            # What rating difference does the market imply?
            market_diff = prob_to_adjem_diff(market_prob)

            # What does our model say?
            model_diff = ratings[team_a] - ratings[team_b]

            # How much should we adjust? (move each team by half the discrepancy)
            error = market_diff - model_diff
            half_err = error / 2.0

            if team_a not in adjustments:
                adjustments[team_a] = []
            if team_b not in adjustments:
                adjustments[team_b] = []

            adjustments[team_a].append(half_err)
            adjustments[team_b].append(-half_err)

        # Apply average adjustment per team (with damping to avoid overreaction)
        DAMPING = 0.6  # How much to trust market vs model (0=ignore market, 1=fully trust)
        for team, adj_list in adjustments.items():
            avg_adj = sum(adj_list) / len(adj_list)
            ratings[team] = ratings[team] + avg_adj * DAMPING

        self.adjusted_ratings = ratings

        # Log significant changes
        for team in sorted(TEAM_RATINGS.keys()):
            original = TEAM_RATINGS[team]
            adjusted = self.adjusted_ratings.get(team, original)
            if abs(adjusted - original) > 1.0:
                log.info(f"Rating adj: {team} {original:.1f} -> {adjusted:.1f} ({adjusted-original:+.1f})")

    def get_matchup_probability(self, team_a: str, team_b: str) -> Optional[float]:
        """
        Get bookmaker-derived probability of team_a beating team_b.
        Returns None if no external data is available for this matchup.
        """
        return self.matchup_overrides.get((team_a, team_b))

    def get_adjusted_rating(self, team: str) -> Optional[float]:
        """Get market-calibrated rating for a team."""
        return self.adjusted_ratings.get(team)

    def get_championship_prob(self, team: str) -> Optional[float]:
        """Get blended championship probability for a team."""
        return self.championship_probs.get(team)

    def get_status(self) -> Dict:
        """Get current status for logging."""
        return {
            "odds_api_credits": self.odds_api.credits_remaining,
            "matchup_overrides": len(self.matchup_overrides) // 2,
            "championship_probs": len(self.championship_probs),
            "kalshi_ws_connected": self.kalshi.ws_connected,
            "kalshi_markets": sum(len(v) for v in self.kalshi.team_markets.values()),
            "adjusted_teams": len(self.adjusted_ratings),
        }

    async def stop(self) -> None:
        """Clean shutdown."""
        await self.kalshi.stop()


# ═══════════════════════════════════════════════════════════════════════════════
# Fair Value Blending
# ═══════════════════════════════════════════════════════════════════════════════

def blend_fair_values(
    mc_fair_values: Dict[str, float],
    championship_probs: Dict[str, float],
    weight_market: float = 0.35,
) -> Dict[str, float]:
    """
    Blend Monte Carlo fair values with market-derived championship probabilities.

    The MC simulation gives expected settlement values (0-64 scale).
    Championship probabilities can be converted to approximate fair values
    using the tournament structure.

    weight_market: how much to weight market data (0.0 = pure MC, 1.0 = pure market)
    """
    if not championship_probs:
        return mc_fair_values

    # Convert championship probability to approximate fair value
    # Using rough expected settlement given championship probability:
    #   P(champ) * 64 + P(runner_up) * 32 + P(F4) * 16 + ...
    # Approximate: EV ≈ P(champ) * 64 * scaling_factor
    # where scaling_factor accounts for settlement value at earlier rounds
    #
    # Empirically: if a team has X% championship probability, their
    # conditional advancement probabilities roughly follow a pattern.
    # A reasonable approximation: FV ≈ P(champ) * 64 * 2.5
    # (because the expected value from intermediate rounds adds ~1.5x)
    #
    # Better: calibrate from our MC output
    total_mc = sum(mc_fair_values.values())

    # Compute market-implied fair values, scaled to match MC total
    market_fvs = {}
    total_market_raw = 0.0
    for team, champ_prob in championship_probs.items():
        # Rough FV from championship prob: use power law scaling
        # Teams with higher champ probability have disproportionately higher FV
        # because they also earn more intermediate round settlements
        market_fvs[team] = champ_prob * 64.0 * 2.2
        total_market_raw += market_fvs[team]

    # Scale market FVs to match MC total (preserves relative ordering)
    if total_market_raw > 0:
        scale = total_mc / total_market_raw
        for team in market_fvs:
            market_fvs[team] *= scale

    # Blend MC and market fair values
    blended = {}
    for team, mc_fv in mc_fair_values.items():
        market_fv = market_fvs.get(team)
        if market_fv is not None:
            blended[team] = (1.0 - weight_market) * mc_fv + weight_market * market_fv
        else:
            blended[team] = mc_fv

    # Preserve total fair value (must sum to ~224 for 68-team tournament)
    total_blended = sum(blended.values())
    if total_blended > 0 and abs(total_blended - total_mc) > 0.5:
        scale = total_mc / total_blended
        blended = {t: v * scale for t, v in blended.items()}

    # Re-sort by value descending
    return dict(sorted(blended.items(), key=lambda x: x[1], reverse=True))


# ═══════════════════════════════════════════════════════════════════════════════
# Standalone test
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    async def test():
        async with aiohttp.ClientSession() as session:
            mgr = OddsManager(session)
            await mgr.initialize()

            print("\n=== Status ===")
            for k, v in mgr.get_status().items():
                print(f"  {k}: {v}")

            print("\n=== Matchup Overrides ===")
            for (a, b), prob in sorted(mgr.matchup_overrides.items()):
                if prob > 0.5:  # Only show one direction
                    print(f"  {a} vs {b}: P({a})={prob:.1%}")

            print("\n=== Championship Probs ===")
            for team, prob in sorted(mgr.championship_probs.items(), key=lambda x: x[1], reverse=True)[:15]:
                print(f"  {team}: {prob:.1%}")

            print("\n=== Rating Adjustments ===")
            from model import TEAM_RATINGS
            for team in sorted(TEAM_RATINGS.keys()):
                orig = TEAM_RATINGS[team]
                adj = mgr.adjusted_ratings.get(team, orig)
                if abs(adj - orig) > 0.5:
                    print(f"  {team}: {orig:.1f} -> {adj:.1f} ({adj-orig:+.1f})")

            await mgr.stop()

    asyncio.run(test())
