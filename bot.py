"""
NCAA Market Madness Trading Bot
Connects to DRW's trading simulator and trades team contracts
based on fair values computed from Monte Carlo tournament simulation.

Strategy:
1. Compute fair values for all 68 teams via Monte Carlo simulation
2. Monitor orderbooks for each contract
3. When market price diverges from fair value by > threshold, trade
4. Use fractional Kelly criterion for position sizing
5. Risk controls: max position, PnL floor, spread limits

Usage:
    Set GAME_ID and TOKEN below, then run:
    python bot.py
"""

import asyncio
import logging
import math
import time
import csv
import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

import aiohttp

from trading_client import Client, Fill, OpenOrder, Order, OrderBook, Trade, create_session
from model import (
    compute_fair_values,
    update_symbol_mapping,
    SYMBOL_TO_TEAM,
    TEAM_TO_SYMBOL,
    TEAM_RATINGS,
    get_team_pace,
    set_matchup_overrides,
    set_adjusted_ratings,
)
from live_data import (
    get_eliminated_teams,
    get_live_games,
    espn_to_model_name,
)
from odds_api import OddsManager, blend_fair_values


# ===== CONFIGURATION =====
GAME_ID = 160
TOKEN = "REDACTED_DRW_TOKEN"
BASE_URL = "https://games.drw.com"

# Trading parameters
MIN_EDGE = 1.5              # Minimum edge in points to trade (contract prices 0-64)
MAX_POSITION = 80           # Max contracts per team (exchange limit is usually 100, leaving buffer)
KELLY_FRACTION = 0.15       # Fractional Kelly multiplier (conservative sizing based on edge)
MAX_ORDER_QTY = 10          # Max contracts per single order execution
SPREAD_LIMIT = 4.0          # Don't trade if spread > this
MIN_PRICE = 0.5             # Don't trade contracts below this price
MAX_PRICE = 63.5            # Don't trade contracts above this price
PNL_FLOOR = -500000         # If Game P&L drops below this, only risk-reducing orders allowed
RECOMPUTE_INTERVAL = 30     # Recompute fair values every N seconds (no live games)
RECOMPUTE_LIVE = 15         # Recompute interval during live games
ESPN_LIVE_INTERVAL = 10     # ESPN live scores check interval (seconds)
ESPN_ELIM_INTERVAL = 60     # ESPN eliminations check interval (seconds)
API_FAIL_THRESHOLD = 3      # Consecutive ESPN failures before pausing trading
ORDER_COOLDOWN = 5.0        # Min seconds between orders on same symbol

# Avellaneda-Stoikov / wash trade parameters
RISK_AVERSION_GAMMA = 0.05    # Avellaneda-Stoikov inventory penalty
WASH_TRADE_SPREAD = 0.5       # Spread threshold for wash trade detection
WASH_TRADE_FV_DIST = 2.0      # Min FV distance to flag wash trading

# Monte Carlo settings
N_SIMULATIONS = 100000

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", mode="a")
    ],
    force=True
)
log = logging.getLogger("bot")

# Set up CSV data logging
def init_csv_logs():
    if not os.path.exists("trades.csv"):
        with open("trades.csv", "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "symbol", "team", "side", "qty", "price", "fair_value", "edge"])
            
    if not os.path.exists("market_data.csv"):
        with open("market_data.csv", "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "symbol", "team", "best_bid", "best_ask", "fair_value", "spread"])

    if not os.path.exists("live_scores_log.csv"):
        with open("live_scores_log.csv", "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "home_team", "away_team", "home_score", "away_score", "clock", "period", "time_remaining_sec"])

init_csv_logs()

def log_live_score_csv(home: str, away: str, h_score: int, a_score: int, clock: str, period: int, time_rem: float):
    try:
        with open("live_scores_log.csv", "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), home, away, h_score, a_score, clock, period, round(time_rem, 1)])
    except Exception:
        pass

def log_trade_csv(symbol: str, team: str, side: str, qty: int, price: float, fv: float):
    try:
        with open("trades.csv", "a", newline="") as f:
            writer = csv.writer(f)
            edge = fv - price if side == "BUY" else price - fv
            writer.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), symbol, team, side, qty, price, round(fv, 2), round(edge, 2)])
    except Exception as e:
        log.error(f"Failed to log trade to CSV: {e}")

def log_market_data_csv(symbol: str, team: str, bid: float, ask: float, fv: float):
    try:
        with open("market_data.csv", "a", newline="") as f:
            writer = csv.writer(f)
            spread = ask - bid if bid and ask else 0
            writer.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), symbol, team, bid, ask, round(fv, 2), round(spread, 2)])
    except Exception:
        pass


@dataclass
class SymbolState:
    """Track trading state for a single symbol."""
    fair_value: float = 0.0
    last_order_time: float = 0.0
    buy_orders: int = 0
    sell_orders: int = 0


class MadnessBot(Client):
    def __init__(
        self,
        session: aiohttp.ClientSession,
        game_id: int,
        token: str,
        base_url: str = BASE_URL,
    ) -> None:
        super().__init__(session, game_id, token, base_url=base_url)
        self.fair_values: Dict[str, float] = {}
        self.symbol_states: Dict[str, SymbolState] = {}
        self.known_symbols: Set[str] = set()
        self.last_recompute: float = 0.0
        self.trading_enabled: bool = True
        self.total_trades: int = 0
        self.matched_symbols: Dict[str, str] = {}  # symbol -> team name
        self.eliminated_teams: Set[str] = set()
        self.last_espn_live: float = 0.0       # Last live scores check
        self.last_espn_elim: float = 0.0       # Last eliminations check
        self.last_market_data_log: float = 0.0
        self.espn_consecutive_fails: int = 0   # Consecutive ESPN API failures
        self.api_paused: bool = False           # Trading paused due to API failure
        self.live_games_map: Dict = {}          # team -> live game info
        self.data_stale: bool = False           # Whether ESPN data is stale
        self.risk_reducing_only: bool = False   # PnL floor failsafe flag

    async def on_start(self) -> None:
        """Initialize and start the trading loop."""
        log.info("Bot starting...")
        log.info(f"Web view: {self.web_url}")

        # Initialize external odds manager (The Odds API + Kalshi)
        log.info("Initializing external odds feeds...")
        self.odds_manager = OddsManager(self.session)
        try:
            await self.odds_manager.initialize()
            # Push external odds into the model
            if self.odds_manager.matchup_overrides:
                set_matchup_overrides(self.odds_manager.matchup_overrides)
                log.info(f"Loaded {len(self.odds_manager.matchup_overrides)//2} matchup overrides from bookmakers")
            if self.odds_manager.adjusted_ratings:
                set_adjusted_ratings(self.odds_manager.adjusted_ratings)
                log.info(f"Loaded market-calibrated ratings for {len(self.odds_manager.adjusted_ratings)} teams")
            odds_status = self.odds_manager.get_status()
            log.info(f"Odds status: credits={odds_status['odds_api_credits']}, "
                     f"matchups={odds_status['matchup_overrides']}, "
                     f"champ_probs={odds_status['championship_probs']}, "
                     f"kalshi_ws={'connected' if odds_status['kalshi_ws_connected'] else 'disconnected'}")
        except Exception as e:
            log.warning(f"External odds init failed (will use hardcoded ratings): {e}")
            self.odds_manager = None

        # Check for already-eliminated teams from ESPN
        log.info("Checking ESPN for eliminated teams...")
        try:
            eliminated, results = await get_eliminated_teams(self.session)
            for espn_name in eliminated:
                model_name = espn_to_model_name(espn_name)
                if model_name:
                    self.eliminated_teams.add(model_name)
            if self.eliminated_teams:
                log.info(f"Already eliminated: {self.eliminated_teams}")
        except Exception as e:
            log.warning(f"Could not fetch ESPN data: {e}")

        # Initial fair value computation (now uses market-calibrated ratings)
        log.info(f"Computing fair values ({N_SIMULATIONS} simulations)...")
        self.fair_values = compute_fair_values(N_SIMULATIONS, self.eliminated_teams)

        # Blend with championship market probabilities if available
        if self.odds_manager and self.odds_manager.championship_probs:
            self.fair_values = blend_fair_values(self.fair_values, self.odds_manager.championship_probs)
            log.info("Blended MC fair values with market championship probabilities")

        self.last_recompute = time.time()

        log.info("Top 10 fair values:")
        for i, (team, fv) in enumerate(self.fair_values.items()):
            if i >= 10:
                break
            log.info(f"  {team:<25} = {fv:.2f}")

        # Discover symbols from orderbook
        await self._discover_symbols()

        # Main trading loop
        while True:
            try:
                await self._trading_cycle()
            except Exception as e:
                log.error(f"Error in trading cycle: {e}")
            await asyncio.sleep(1)

    async def _discover_symbols(self) -> None:
        """Discover exchange symbols and match to team names."""
        order_books = await self.get_order_books()
        symbols = list(order_books.keys())
        self.known_symbols = set(symbols)

        log.info(f"Found {len(symbols)} symbols on exchange")

        # Try automatic mapping
        update_symbol_mapping(symbols)
        self.matched_symbols = dict(SYMBOL_TO_TEAM)

        # Log mappings
        matched = len(self.matched_symbols)
        log.info(f"Auto-matched {matched}/{len(symbols)} symbols to teams")

        # For unmatched symbols, log them so user can manually map
        unmatched = [s for s in symbols if s not in self.matched_symbols]
        if unmatched:
            log.warning(f"Unmatched symbols: {unmatched[:20]}")
            # Try to match remaining by being more aggressive
            self._aggressive_match(unmatched)

        # Initialize symbol states with fair values
        for symbol in symbols:
            team = self.matched_symbols.get(symbol)
            fv = self.fair_values.get(team, 0.0) if team else 0.0
            self.symbol_states[symbol] = SymbolState(fair_value=fv)

        # Log final mapping stats
        mapped_count = sum(1 for s in symbols if s in self.matched_symbols)
        log.info(f"Final mapping: {mapped_count}/{len(symbols)} symbols matched")

    def _aggressive_match(self, unmatched: List[str]) -> None:
        """Try harder to match symbols to teams."""
        # Exact exchange symbol -> model team name mapping
        # (discovered from live orderbook)
        exact_map = {
            "Akron": "Akron", "Alabama": "Alabama", "Arizona": "Arizona",
            "Arkansas": "Arkansas", "BYU": "BYU", "Cal Baptist": "Cal Baptist",
            "Clemson": "Clemson", "Duke": "Duke", "Florida": "Florida",
            "Furman": "Furman", "Georgia": "Georgia", "Gonzaga": "Gonzaga",
            "Hawaii": "Hawaii", "High Point": "High Point", "Hofstra": "Hofstra",
            "Houston": "Houston", "Howard": "Howard", "Idaho": "Idaho",
            "Illinois": "Illinois", "Iowa": "Iowa", "Iowa St": "Iowa State",
            "Kansas": "Kansas", "Kennesaw St": "Kennesaw State",
            "Kentucky": "Kentucky", "Lehigh": "Lehigh",
            "Long Island": "Long Island", "Louisville": "Louisville",
            "McNeese": "McNeese", "Miami FL": "Miami FL", "Miami OH": "Miami OH",
            "Michigan": "Michigan", "Michigan St": "Michigan State",
            "Missouri": "Missouri", "NC State": "NC State",
            "Nebraska": "Nebraska", "North Carolina": "North Carolina",
            "North Dakota St": "North Dakota State",
            "Northern Iowa": "Northern Iowa", "Ohio St": "Ohio State",
            "Penn": "Penn", "Prairie View": "Prairie View A&M",
            "Purdue": "Purdue", "Queens": "Queens", "SMU": "SMU",
            "Saint Louis": "Saint Louis", "Saint Marys": "Saint Mary's",
            "Santa Clara": "Santa Clara", "Siena": "Siena",
            "South Florida": "South Florida", "St Johns": "St. John's",
            "TCU": "TCU", "Tennessee": "Tennessee",
            "Tennessee St": "Tennessee State", "Texas": "Texas",
            "Texas A&M": "Texas A&M", "Texas Tech": "Texas Tech",
            "Troy": "Troy", "UCF": "UCF", "UCLA": "UCLA",
            "UConn": "Connecticut", "UMBC": "UMBC", "Utah St": "Utah State",
            "VCU": "VCU", "Vanderbilt": "Vanderbilt",
            "Villanova": "Villanova", "Virginia": "Virginia",
            "Wisconsin": "Wisconsin", "Wright St": "Wright State",
        }

        for symbol in unmatched:
            # Check exact exchange map first
            if symbol in exact_map:
                team = exact_map[symbol]
                TEAM_TO_SYMBOL[team] = symbol
                SYMBOL_TO_TEAM[symbol] = team
                self.matched_symbols[symbol] = team
                continue

            # Fuzzy fallback - sort by length descending so "Michigan State"
            # is checked before "Michigan" (prevents substring false matches)
            import re
            sym_lower = symbol.lower()
            for team in sorted(TEAM_RATINGS.keys(), key=len, reverse=True):
                team_lower = team.lower()
                if re.search(r'\b' + re.escape(team_lower) + r'\b', sym_lower):
                    if team not in TEAM_TO_SYMBOL:
                        TEAM_TO_SYMBOL[team] = symbol
                        SYMBOL_TO_TEAM[symbol] = team
                        self.matched_symbols[symbol] = team
                        break

    async def _trading_cycle(self) -> None:
        """One iteration of the trading loop."""
        
        # Parse clock helper
        def parse_clock(clock_str: str, period: int) -> float:
            try:
                if ":" in clock_str:
                    m, s = clock_str.split(":")
                    mins, secs = int(m), int(s)
                else:
                    mins, secs = 0, int(float(clock_str))
                
                if period == 1:
                    return (20 * 60) + (mins * 60 + secs)
                elif period == 2:
                    return (mins * 60 + secs)
                elif period > 2:
                    return (mins * 60 + secs)
            except:
                pass
            return 0.0

        now = time.time()

        # --- ESPN Live Scores (every 10s) ---
        if now - self.last_espn_live > ESPN_LIVE_INTERVAL:
            self.last_espn_live = now
            try:
                # Get today's live games
                live_games_espn = await get_live_games(self.session)
                
                # Check yesterday's games too, in case of late games crossing midnight
                from datetime import datetime, timedelta
                yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
                import live_data
                yesterday_all = await live_data.fetch_tournament_scores(self.session, yesterday)
                yesterday_live = [g for g in yesterday_all if g.get("status") in ("STATUS_IN_PROGRESS", "STATUS_HALFTIME", "STATUS_END_PERIOD")]
                
                # Merge and deduplicate
                seen_ids = {g.get('id') for g in live_games_espn}
                for g in yesterday_live:
                    if g.get('id') not in seen_ids:
                        live_games_espn.append(g)

                live_games_map = {}
                for g in live_games_espn:
                    home = espn_to_model_name(g.get('home_team', ''))
                    away = espn_to_model_name(g.get('away_team', ''))
                    if not home or not away:
                        continue

                    diff = g.get('home_score', 0) - g.get('away_score', 0)
                    time_remaining = parse_clock(g.get('clock', '0:00'), g.get('period', 1))

                    # Log the exact score ping pinged by the engine
                    log_live_score_csv(
                        home, away, 
                        g.get('home_score', 0), g.get('away_score', 0), 
                        g.get('clock', '0:00'), g.get('period', 1), 
                        time_remaining
                    )

                    home_pace = get_team_pace(home)
                    away_pace = get_team_pace(away)
                    avg_pace = (home_pace + away_pace) / 2.0

                    live_games_map[home] = {'opponent': away, 'score_diff': diff, 'time_remaining': time_remaining, 'pace': avg_pace}
                    live_games_map[away] = {'opponent': home, 'score_diff': -diff, 'time_remaining': time_remaining, 'pace': avg_pace}

                self.live_games_map = live_games_map
                self.data_stale = False

                # Reset failure counter on success
                if self.espn_consecutive_fails > 0:
                    log.info(f"ESPN API recovered after {self.espn_consecutive_fails} failures")
                self.espn_consecutive_fails = 0
                if self.api_paused:
                    self.api_paused = False
                    log.info("API recovered - RESUMING trading")

            except Exception as e:
                self.espn_consecutive_fails += 1
                log.warning(f"ESPN live check failed ({self.espn_consecutive_fails}x consecutive): {e}")

                if self.espn_consecutive_fails >= API_FAIL_THRESHOLD:
                    self.data_stale = True
                    if not self.api_paused:
                        self.api_paused = True
                        log.critical(
                            f"ESPN API FAILED {self.espn_consecutive_fails}x in a row - "
                            f"PAUSING ALL TRADING until API recovers. "
                            f"Will NOT trade on stale data."
                        )

        # --- ESPN Eliminations (every 120s, separate from live scores) ---
        if now - self.last_espn_elim > ESPN_ELIM_INTERVAL:
            self.last_espn_elim = now
            try:
                eliminated, _ = await get_eliminated_teams(self.session)
                new_elims = set()
                for espn_name in eliminated:
                    model_name = espn_to_model_name(espn_name)
                    if model_name and model_name not in self.eliminated_teams:
                        new_elims.add(model_name)
                        self.eliminated_teams.add(model_name)
                if new_elims:
                    log.info(f"NEW ELIMINATIONS: {new_elims}")
                    self.last_recompute = 0
            except Exception as e:
                log.debug(f"ESPN elimination check failed: {e}")

        # --- API Failsafe: block all trading if API is down ---
        if self.api_paused:
            return

        # --- Refresh external odds periodically ---
        has_live = bool(getattr(self, 'live_games_map', {}))
        if getattr(self, 'odds_manager', None):
            try:
                odds_updated = await self.odds_manager.refresh(has_live_games=has_live)
                if odds_updated:
                    # Push fresh odds into model
                    if self.odds_manager.matchup_overrides:
                        set_matchup_overrides(self.odds_manager.matchup_overrides)
                    if self.odds_manager.adjusted_ratings:
                        set_adjusted_ratings(self.odds_manager.adjusted_ratings)
                    log.info("External odds refreshed and pushed to model")
            except Exception as e:
                log.debug(f"Odds refresh failed (non-critical): {e}")

        # Check if we should recompute fair values (faster during live games)
        recompute_interval = RECOMPUTE_LIVE if has_live else RECOMPUTE_INTERVAL
        if time.time() - self.last_recompute > recompute_interval:
            log.info("Recomputing fair values...")
            self.fair_values = compute_fair_values(N_SIMULATIONS, self.eliminated_teams, getattr(self, 'live_games_map', None))

            # Blend with market championship probs
            if getattr(self, 'odds_manager', None) and self.odds_manager.championship_probs:
                self.fair_values = blend_fair_values(self.fair_values, self.odds_manager.championship_probs)

            self.last_recompute = time.time()
            # Update symbol states
            for symbol, state in self.symbol_states.items():
                team = self.matched_symbols.get(symbol)
                if team:
                    state.fair_value = self.fair_values.get(team, 0.0)
            # Cancel ALL outstanding limit orders after FV recompute
            # to prevent stale orders from being picked off at old prices
            try:
                open_orders = await self.get_open_orders()
                if open_orders:
                    await self.cancel_orders(list(open_orders.keys()))
                    log.info(f"Cancelled {len(open_orders)} stale orders after FV recompute")
            except Exception as e:
                log.warning(f"Failed to cancel stale orders: {e}")

        # Check explicit -500,000 PnL limit rule
        game_pnl = getattr(self, 'cash', 0.0) + getattr(self, 'margin', 0.0)
        was_risk_reducing = getattr(self, 'risk_reducing_only', False)
        self.risk_reducing_only = game_pnl < PNL_FLOOR

        if self.risk_reducing_only and not was_risk_reducing:
            log.warning(f"Game PnL ({game_pnl:.0f}) breached floor ({PNL_FLOOR}). Switching to risk-reducing only mode.")

        if not self.trading_enabled:
            return

        # Evaluate each symbol
        order_books = self.order_books
        
        # Log market data periodically (e.g., every 60 seconds)
        log_market = False
        if time.time() - self.last_market_data_log > 60:
            log_market = True
            self.last_market_data_log = time.time()
            
        for symbol, book in order_books.items():
            if symbol not in self.matched_symbols:
                continue

            state = self.symbol_states.get(symbol)
            if not state or state.fair_value <= 0.01:
                continue
                
            if log_market:
                bid = book.best_bid_px or 0.0
                ask = book.best_ask_px or 0.0
                log_market_data_csv(symbol, self.matched_symbols[symbol], bid, ask, state.fair_value)

            # Cooldown check
            if time.time() - state.last_order_time < ORDER_COOLDOWN:
                continue

            await self._evaluate_and_trade(symbol, book, state)

    async def _evaluate_and_trade(
        self, symbol: str, book: OrderBook, state: SymbolState
    ) -> None:
        """Evaluate a single symbol and potentially trade."""
        fair_value = state.fair_value
        team_name = self.matched_symbols.get(symbol, "")

        # Stale data filter
        if getattr(self, 'data_stale', False):
            return

        if book.best_ask_px is None and book.best_bid_px is None:
            return

        best_bid = book.best_bid_px or 0.0
        best_ask = book.best_ask_px or 64.0
        spread = best_ask - best_bid

        # --- Wash Trade Detection ---
        if spread <= WASH_TRADE_SPREAD and spread > 0:
            market_mid = (best_bid + best_ask) / 2.0
            if abs(market_mid - fair_value) > WASH_TRADE_FV_DIST:
                return  # Skip - toxic liquidity illusion

        # Determine time remaining and Option Value (OV)
        time_rem = 2400.0  # assume pre-game full 40 mins
        live_games = getattr(self, 'live_games_map', {})
        if live_games and team_name in live_games:
            time_rem = live_games[team_name].get('time_remaining', 2400.0)

        # Environmental Firewall: Reduce-Only in final 4 minutes
        is_late_game = (0 < time_rem < 240)

        # Spread constraint
        if spread > SPREAD_LIMIT and best_bid > 0 and best_ask < 64:
            return

        # Current position in this symbol
        position = self.positions.get(symbol, 0)

        # --- Avellaneda-Stoikov: penalize FV based on inventory ---
        inventory_penalty = position * RISK_AVERSION_GAMMA
        skewed_fv = fair_value - inventory_penalty

        # Enhanced OV: only during live games (pre-game FV already captures uncertainty via MC)
        is_live_game = (live_games and team_name in live_games and time_rem < 2400.0)
        if is_live_game:
            p = min(max(fair_value / 64.0, 0.0), 1.0)
            N = time_rem / 120.0  # discrete evaluation blocks remaining
            if N > 0:
                option_value = 0.39 * math.pow(N, 0.42) * (1.0 + 16.12 * p * (1.0 - p))
            else:
                option_value = 0.0
        else:
            option_value = 0.0

        # Price boundaries (10% to 90%)
        hard_min_px = 6.4
        hard_max_px = 57.6

        # === BUY LOGIC ===
        if book.best_ask_px is not None and position < MAX_POSITION:
            ask = book.best_ask_px
            # Calculate intrinsic edge using skewed FV
            buy_edge = skewed_fv - ask

            # Opening new long: require MIN_EDGE + option_value
            # Closing short (position < 0): require only MIN_EDGE
            if position < 0:
                effective_req = MIN_EDGE
            else:
                effective_req = MIN_EDGE + option_value

            if buy_edge > effective_req and hard_min_px <= ask <= hard_max_px:
                # If late game, only buy if we are short (reducing risk). Don't open/add longs.
                if not (is_late_game and position >= 0):
                    # Compute Kelly-optimal size (use skewed FV for consistency with edge calc)
                    qty = self._compute_order_qty(skewed_fv, ask, position, side="buy")
                    if qty > 0:
                        try:
                            order = await self.send_order(symbol, ask, qty, "LIMIT")
                            state.last_order_time = time.time()
                            self.total_trades += 1
                            team = self.matched_symbols.get(symbol, symbol)
                            log.info(
                                f"BUY  {qty:>3}x {team:<20} @ {ask:>5.1f}  "
                                f"(FV={fair_value:.1f}, skewed={skewed_fv:.1f}, edge={buy_edge:.1f}, "
                                f"OV={option_value:.2f}, req={effective_req:.1f}, pos={position})"
                            )
                        except Exception as e:
                            log.error(f"Buy order failed for {symbol}: {e}")

        # === SELL LOGIC ===
        if book.best_bid_px is not None and position > -MAX_POSITION:
            bid = book.best_bid_px
            # Calculate intrinsic edge using skewed FV
            sell_edge = bid - skewed_fv

            # Opening new short: require MIN_EDGE + option_value
            # Closing long (position > 0): require only MIN_EDGE
            if position > 0:
                effective_req = MIN_EDGE
            else:
                effective_req = MIN_EDGE + option_value

            if sell_edge > effective_req and hard_min_px <= bid <= hard_max_px:
                # If late game, only sell if we are long (reducing risk). Don't open new shorts.
                if not (is_late_game and position <= 0):
                    qty = self._compute_order_qty(skewed_fv, bid, position, side="sell")
                    if qty > 0:
                        try:
                            order = await self.send_order(symbol, bid, -qty, "LIMIT")
                            state.last_order_time = time.time()
                            self.total_trades += 1
                            team = self.matched_symbols.get(symbol, symbol)
                            log.info(
                                f"SELL {qty:>3}x {team:<20} @ {bid:>5.1f}  "
                                f"(FV={fair_value:.1f}, skewed={skewed_fv:.1f}, edge={sell_edge:.1f}, "
                                f"OV={option_value:.2f}, req={effective_req:.1f}, pos={position})"
                            )
                        except Exception as e:
                            log.error(f"Sell order failed for {symbol}: {e}")

        # === MARKET MAKING (post limit orders near fair value) ===
        # Only market-make once at least one game is live (avoid getting picked off pre-game)
        has_any_live = bool(getattr(self, 'live_games_map', {}))
        if not is_late_game and has_any_live:
            await self._post_limit_orders(symbol, book, state, position, skewed_fv)

    async def _post_limit_orders(
        self, symbol: str, book: OrderBook, state: SymbolState, position: int,
        skewed_fv: Optional[float] = None
    ) -> None:
        """
        Post limit orders around fair value to provide liquidity and earn spread.
        Only posts if there's a gap in the book we can fill profitably.
        Uses inventory-skewed fair value when available.
        """
        fv = skewed_fv if skewed_fv is not None else state.fair_value
        if fv < MIN_PRICE or fv > MAX_PRICE:
            return

        # Cancel existing orders first to avoid stacking
        try:
            open_orders = await self.get_open_orders()
            symbol_orders = [
                oid for oid, o in open_orders.items()
                if o.display_symbol == symbol
            ]
            if symbol_orders:
                await self.cancel_orders(symbol_orders)
        except Exception:
            pass

        half_spread = max(MIN_EDGE, 1.5)

        # Post a bid below fair value (if we have room to buy)
        if position < MAX_POSITION:
            bid_px = round(fv - half_spread, 1)
            # Don't bid above existing best bid unless we have edge
            if book.best_bid_px is not None:
                bid_px = min(bid_px, book.best_bid_px + 0.1)
            if bid_px >= MIN_PRICE and bid_px < fv:
                bid_qty = min(3, MAX_POSITION - position)
                if getattr(self, "risk_reducing_only", False):
                    if position >= 0:
                        bid_qty = 0
                    else:
                        bid_qty = min(bid_qty, -position)
                if bid_qty > 0:
                    try:
                        await self.send_order(symbol, bid_px, bid_qty, "LIMIT")
                    except Exception:
                        pass

        # Post an ask above fair value (if we have room to sell)
        if position > -MAX_POSITION:
            ask_px = round(fv + half_spread, 1)
            # Don't ask below existing best ask unless we have edge
            if book.best_ask_px is not None:
                ask_px = max(ask_px, book.best_ask_px - 0.1)
            if ask_px <= MAX_PRICE and ask_px > fv:
                ask_qty = min(3, MAX_POSITION + position)
                if getattr(self, "risk_reducing_only", False):
                    if position <= 0:
                        ask_qty = 0
                    else:
                        ask_qty = min(ask_qty, position)
                if ask_qty > 0:
                    try:
                        await self.send_order(symbol, ask_px, -ask_qty, "LIMIT")
                    except Exception:
                        pass

    def _compute_order_qty(
        self, fair_value: float, price: float, position: int, side: str
    ) -> int:
        """
        Compute order quantity using fractional Kelly criterion.
        Adapted for multi-outcome settlement (not binary).
        """
        # Risk reduction restriction
        if getattr(self, "risk_reducing_only", False):
            if side == "buy" and position >= 0:
                return 0
            if side == "sell" and position <= 0:
                return 0
        if side == "buy":
            # Edge = fair_value - price
            edge = fair_value - price
            if edge <= 0:
                return 0

            # Simplified Kelly for non-binary: edge / variance
            # Approximate variance from settlement distribution
            # For a contract with fair_value fv, max settlement 64:
            # Rough variance ≈ fv * (64 - fv) (similar to binary p(1-p) scaled)
            variance = max(fair_value * (64 - fair_value), 1.0)
            kelly_fraction = edge / variance * 64  # Scale to contract units

            # Increased base multiplier to allow hitting MAX_ORDER_QTY on large edges
            raw_qty = kelly_fraction * KELLY_FRACTION * 500  
            qty = max(1, min(int(raw_qty), MAX_ORDER_QTY))

            # Position limit check
            max_buy = MAX_POSITION - position
            if getattr(self, "risk_reducing_only", False) and position < 0:
                max_buy = min(max_buy, -position)
                
            if max_buy <= 0:
                return 0
            return min(qty, max_buy)

        else:  # sell
            edge = price - fair_value
            if edge <= 0:
                return 0

            variance = max(fair_value * (64 - fair_value), 1.0)
            kelly_fraction = edge / variance * 64

            # Increased base multiplier to allow hitting MAX_ORDER_QTY on large edges
            raw_qty = kelly_fraction * KELLY_FRACTION * 500
            qty = max(1, min(int(raw_qty), MAX_ORDER_QTY))

            # Position limit check (can go short)
            max_sell = MAX_POSITION + position
            if getattr(self, "risk_reducing_only", False) and position > 0:
                max_sell = min(max_sell, position)

            if max_sell <= 0:
                return 0
            return min(qty, max_sell)

    # === EVENT HANDLERS ===

    async def on_orderbook_updates(self, order_books: Dict[str, OrderBook]) -> None:
        """Log significant orderbook changes only - main loop handles trading."""
        pass

    async def on_fills(self, new_fills: List[Fill]) -> None:
        """Log fills."""
        for fill in new_fills:
            team = self.matched_symbols.get(fill.display_symbol, fill.display_symbol)
            side = "BUY" if fill.traded_qty > 0 else "SELL"
            log.info(
                f"FILL: {side} {abs(fill.traded_qty)}x {team} @ {fill.px:.1f} "
                f"(remaining: {fill.remaining_qty})"
            )
            # Log to CSV
            state = self.symbol_states.get(fill.display_symbol)
            fv = state.fair_value if state else 0.0
            log_trade_csv(fill.display_symbol, team, side, abs(fill.traded_qty), fill.px, fv)

    async def on_order_update(self, order: Order) -> None:
        """Log order updates."""
        if order.canceled:
            log.debug(f"Order {order.order_id} canceled: {order.display_symbol}")

    async def on_all_trade(self, trade: Trade) -> None:
        """Monitor all trades for market intelligence."""
        team = self.matched_symbols.get(trade.display_symbol, trade.display_symbol)
        state = self.symbol_states.get(trade.display_symbol)
        if state:
            fv = state.fair_value
            diff = trade.px - fv
            if abs(diff) > 3.0:
                log.info(
                    f"MARKET: {team} traded @ {trade.px:.1f} "
                    f"(FV={fv:.1f}, diff={diff:+.1f})"
                )

    async def on_notification(self, message: str) -> None:
        """Handle notifications - may include game results."""
        log.info(f"NOTIFICATION: {message}")
        # If a game result notification comes through, we should
        # update our model. For now, recompute fair values.
        if any(kw in message.lower() for kw in ["eliminated", "advances", "final", "wins", "settled"]):
            log.info("Game result detected - will recompute fair values on next cycle...")
            self.last_recompute = 0  # Force recompute on next cycle

    async def on_error(self, error: str) -> None:
        """Handle errors - log but don't exit for recoverable errors."""
        log.error(f"Server error: {error}")
        if "banned" in error.lower() or "terminated" in error.lower():
            self.trading_enabled = False
            log.critical("FATAL: Bot may be banned. Stopping all trading.")


# === STATUS DISPLAY ===

async def print_status(bot: MadnessBot) -> None:
    """Periodically print bot status."""
    while True:
        await asyncio.sleep(30)
        pos_count = len(bot.positions)
        pos_value = sum(
            qty * bot.symbol_states.get(sym, SymbolState()).fair_value
            for sym, qty in bot.positions.items()
        )
        odds_info = ""
        if getattr(bot, 'odds_manager', None):
            status = bot.odds_manager.get_status()
            odds_info = (f" | OddsAPI credits={status['odds_api_credits']} | "
                        f"Matchups={status['matchup_overrides']} | "
                        f"Kalshi={'WS' if status['kalshi_ws_connected'] else 'REST'}")
        log.info(
            f"STATUS: Cash={bot.cash:.0f} | Positions={pos_count} | "
            f"PositionValue~{pos_value:.0f} | Trades={bot.total_trades} | "
            f"Trading={'ON' if bot.trading_enabled else 'OFF'}{odds_info}"
        )


# === ENTRY POINT ===

async def main():
    if TOKEN == "YOUR_TOKEN_HERE":
        print("=" * 60)
        print("DRW Market Madness Trading Bot")
        print("=" * 60)
        print()
        print("Before running, you need to:")
        print(f"  1. Register at {BASE_URL}/games/trading-simulator/{GAME_ID}")
        print("     Access code: DRWMarchMadness26!")
        print("  2. Get your API token from the web interface")
        print("  3. Set TOKEN in this file (bot.py)")
        print()
        print("Running in PREVIEW MODE - showing fair values only:")
        print()

        fair_values = compute_fair_values(N_SIMULATIONS)
        print(f"{'Team':<25} {'Fair Value':>10}")
        print("-" * 38)
        for team, fv in fair_values.items():
            if fv >= 0.01:
                print(f"{team:<25} {fv:>10.2f}")
        return

    async with create_session() as session:
        bot = MadnessBot(session, GAME_ID, TOKEN, BASE_URL)
        log.info(f"Access web view at {bot.web_url}")

        # Register if needed
        try:
            await bot.register()
            log.info("Registered successfully")
        except Exception as e:
            log.info(f"Registration: {e} (may already be registered)")

        await bot.start()


if __name__ == "__main__":
    asyncio.run(main())
