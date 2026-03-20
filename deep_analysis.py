"""
Deep NCAA Market Analysis & Strategy Optimization
- Market microstructure & other participants' behavior
- Wash trading detection
- Multi-strategy backtesting & optimization
- Today's games & trading recommendations
"""

import csv
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from itertools import product

# ============================================================
# DATA LOADING
# ============================================================

def load_all_market_data(filename="market_data.csv"):
    """Load ALL market data rows."""
    data = []
    with open(filename, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                row['best_bid'] = float(row['best_bid']) if row.get('best_bid') else 0
                row['best_ask'] = float(row['best_ask']) if row.get('best_ask') else 0
                row['fair_value'] = float(row['fair_value'])
                row['spread'] = float(row['spread']) if row.get('spread') else 0
                data.append(row)
            except (ValueError, KeyError):
                continue
    return data

def load_trades(filename="trades.csv"):
    trades = []
    with open(filename, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                row['qty'] = int(row['qty'])
                row['price'] = float(row['price'])
                row['fair_value'] = float(row['fair_value'])
                row['edge'] = float(row['edge'])
                trades.append(row)
            except (ValueError, KeyError):
                continue
    return trades

print("Loading data (this may take a moment)...")
market_data = load_all_market_data()
trades = load_trades()
print(f"Loaded {len(market_data)} market snapshots, {len(trades)} trades\n")

# ============================================================
# PART 1: MARKET MICROSTRUCTURE ANALYSIS
# ============================================================

print("=" * 90)
print("PART 1: MARKET MICROSTRUCTURE & OTHER PARTICIPANTS' BEHAVIOR")
print("=" * 90)

# Group market data by team and track price movements
team_snapshots = defaultdict(list)
for row in market_data:
    team = row.get('team', '')
    if team:
        team_snapshots[team].append(row)

# --- 1A: Price vs Fair Value divergence over time ---
print("\n--- 1A: MARKET PRICING vs MODEL FAIR VALUE ---")
print("(How much does the market disagree with our model?)\n")

team_mispricing = {}
for team, snaps in team_snapshots.items():
    if len(snaps) < 10:
        continue
    mid_prices = []
    fvs = []
    for s in snaps:
        bid, ask, fv = s['best_bid'], s['best_ask'], s['fair_value']
        if bid > 0 and ask > 0 and ask < 64:
            mid = (bid + ask) / 2
            mid_prices.append(mid)
            fvs.append(fv)

    if not mid_prices:
        continue

    # Average mispricing: how much market mid deviates from our FV
    avg_mid = statistics.mean(mid_prices)
    avg_fv = statistics.mean(fvs)
    avg_diff = avg_mid - avg_fv

    # Persistent direction? Market consistently above or below FV?
    above_count = sum(1 for m, f in zip(mid_prices, fvs) if m > f)
    pct_above = above_count / len(mid_prices) * 100

    team_mispricing[team] = {
        'avg_mid': avg_mid, 'avg_fv': avg_fv, 'avg_diff': avg_diff,
        'pct_above': pct_above, 'n_snaps': len(mid_prices)
    }

print(f"{'Team':<22} {'Avg Mid':>8} {'Avg FV':>8} {'Diff':>7} {'Mkt>FV%':>8} {'Snaps':>6}")
print("-" * 65)
for team, d in sorted(team_mispricing.items(), key=lambda x: abs(x[1]['avg_diff']), reverse=True)[:25]:
    print(f"{team:<22} {d['avg_mid']:>8.2f} {d['avg_fv']:>8.2f} {d['avg_diff']:>+7.2f} {d['pct_above']:>7.1f}% {d['n_snaps']:>6}")

# --- 1B: Spread Analysis (who's providing liquidity?) ---
print("\n--- 1B: SPREAD ANALYSIS (Liquidity & Market Making Opportunities) ---\n")

team_spreads = {}
for team, snaps in team_snapshots.items():
    spreads = [s['spread'] for s in snaps if s['spread'] > 0 and s['best_ask'] < 64]
    if spreads:
        team_spreads[team] = {
            'avg_spread': statistics.mean(spreads),
            'median_spread': statistics.median(spreads),
            'min_spread': min(spreads),
            'max_spread': max(spreads),
            'pct_tight': sum(1 for s in spreads if s <= 0.5) / len(spreads) * 100,
            'pct_wide': sum(1 for s in spreads if s > 2.0) / len(spreads) * 100,
        }

print(f"{'Team':<22} {'Avg Spd':>8} {'Med Spd':>8} {'Min':>6} {'<0.5%':>7} {'>2.0%':>7}")
print("-" * 62)

# Show widest spreads first (market making opportunities)
for team, d in sorted(team_spreads.items(), key=lambda x: x[1]['avg_spread'], reverse=True)[:20]:
    print(f"{team:<22} {d['avg_spread']:>8.2f} {d['median_spread']:>8.2f} {d['min_spread']:>6.2f} {d['pct_tight']:>6.1f}% {d['pct_wide']:>6.1f}%")

print("\n  >> MARKET MAKING OPPORTUNITY: Teams with wide spreads + high FV")
print("     (profitable to post bids below FV and asks above FV)")
for team, d in sorted(team_spreads.items(), key=lambda x: x[1]['avg_spread'], reverse=True):
    fv = team_mispricing.get(team, {}).get('avg_fv', 0)
    if d['avg_spread'] > 1.0 and fv > 3.0:
        edge_per_rt = d['avg_spread'] / 2  # rough half-spread capture
        print(f"     {team:<20} avg_spread={d['avg_spread']:.2f} FV={fv:.1f} est_edge/roundtrip=${edge_per_rt:.2f}")

# --- 1C: Wash Trading Detection ---
print("\n--- 1C: WASH TRADING DETECTION ---")
print("(Looking for suspicious patterns: rapid same-price trades, bid=ask, etc.)\n")

# Detect wash trading signals in market data
wash_signals = defaultdict(list)

for team, snaps in team_snapshots.items():
    for i in range(1, len(snaps)):
        prev = snaps[i-1]
        curr = snaps[i]

        bid, ask = curr['best_bid'], curr['best_ask']
        prev_bid, prev_ask = prev['best_bid'], prev['best_ask']

        # Signal 1: Bid == Ask (crossed/locked market - very suspicious)
        if bid > 0 and ask > 0 and bid >= ask:
            wash_signals[team].append(('LOCKED_MARKET', curr['timestamp'], f"bid={bid} >= ask={ask}"))

        # Signal 2: Spread is exactly 0.01 and price far from FV (artificial)
        if curr['spread'] == 0.01 and abs(bid - curr['fair_value']) > 2.0:
            wash_signals[team].append(('ARTIFICIAL_TIGHT', curr['timestamp'], f"spread=0.01, bid={bid}, FV={curr['fair_value']:.2f}"))

# Report wash trading signals
teams_with_wash = {t: sigs for t, sigs in wash_signals.items() if len(sigs) > 5}
if teams_with_wash:
    print(f"Teams with potential wash trading signals (>5 instances):")
    for team, sigs in sorted(teams_with_wash.items(), key=lambda x: len(x[1]), reverse=True)[:15]:
        sig_types = defaultdict(int)
        for sig_type, _, _ in sigs:
            sig_types[sig_type] += 1
        sig_summary = ", ".join(f"{k}:{v}" for k, v in sig_types.items())
        print(f"  {team:<22} {len(sigs):>4} signals ({sig_summary})")
        # Show first few examples
        for sig_type, ts, detail in sigs[:3]:
            print(f"    -> {ts} {sig_type}: {detail}")
else:
    print("  No strong wash trading signals detected.")

# --- 1D: Price Movement Patterns (how do others react?) ---
print("\n--- 1D: PRICE MOVEMENT PATTERNS ---")
print("(How do market prices move relative to FV over time?)\n")

# Track bid/ask changes and classify participant behavior
for team in ['Duke', 'Michigan', 'Arizona', 'Florida', 'Houston', 'Iowa State', 'Connecticut', 'Michigan State']:
    snaps = team_snapshots.get(team, [])
    if len(snaps) < 20:
        continue

    # Calculate price momentum
    bids = [s['best_bid'] for s in snaps if s['best_bid'] > 0]
    asks = [s['best_ask'] for s in snaps if s['best_ask'] > 0]
    fvs = [s['fair_value'] for s in snaps]

    if len(bids) < 10:
        continue

    first_mid = (bids[0] + asks[0]) / 2 if asks[0] > 0 else bids[0]
    last_mid = (bids[-1] + asks[-1]) / 2 if asks[-1] > 0 else bids[-1]
    first_fv = fvs[0]
    last_fv = fvs[-1]

    mid_change = last_mid - first_mid
    fv_change = last_fv - first_fv

    # Detect mean reversion vs momentum
    mid_series = [(bids[i] + asks[i]) / 2 for i in range(min(len(bids), len(asks))) if asks[i] > 0]
    if len(mid_series) > 2:
        returns = [mid_series[i] - mid_series[i-1] for i in range(1, len(mid_series))]
        # Autocorrelation of returns (positive = momentum, negative = mean reversion)
        if len(returns) > 5 and statistics.stdev(returns) > 0:
            mean_r = statistics.mean(returns)
            auto_corr_sum = sum((returns[i] - mean_r) * (returns[i-1] - mean_r) for i in range(1, len(returns)))
            var_sum = sum((r - mean_r) ** 2 for r in returns)
            autocorr = auto_corr_sum / var_sum if var_sum > 0 else 0

            behavior = "MOMENTUM" if autocorr > 0.1 else "MEAN-REVERT" if autocorr < -0.1 else "RANDOM"
            print(f"  {team:<20} mid: {first_mid:.1f}->{last_mid:.1f} ({mid_change:+.1f}) | "
                  f"FV: {first_fv:.1f}->{last_fv:.1f} ({fv_change:+.1f}) | "
                  f"autocorr={autocorr:+.3f} [{behavior}]")

# --- 1E: Exploit Opportunities ---
print("\n--- 1E: EXPLOIT OPPORTUNITIES (Persistent Mispricings) ---\n")

print("Teams where market CONSISTENTLY overprices (>75% of time above FV):")
print("  -> These are SELL opportunities")
for team, d in sorted(team_mispricing.items(), key=lambda x: x[1]['avg_diff'], reverse=True):
    if d['pct_above'] > 75 and d['avg_diff'] > 1.0:
        print(f"  {team:<22} market above FV {d['pct_above']:.0f}% of time, avg overpricing={d['avg_diff']:+.2f}")

print("\nTeams where market CONSISTENTLY underprices (<25% of time above FV):")
print("  -> These are BUY opportunities")
for team, d in sorted(team_mispricing.items(), key=lambda x: x[1]['avg_diff']):
    if d['pct_above'] < 25 and d['avg_diff'] < -1.0:
        print(f"  {team:<22} market above FV {d['pct_above']:.0f}% of time, avg underpricing={d['avg_diff']:+.2f}")


# ============================================================
# PART 2: MULTI-STRATEGY BACKTESTING
# ============================================================

print("\n\n" + "=" * 90)
print("PART 2: MULTI-STRATEGY BACKTESTING")
print("=" * 90)

class Backtester:
    """Backtester that replays market data with configurable strategy parameters."""

    def __init__(self, min_edge=1.5, max_position=80, spread_limit=4.0,
                 min_price=0.5, max_price=63.5, kelly_fraction=0.15,
                 max_order_qty=10, buy_only=False, sell_only=False,
                 favorites_only=False, underdogs_only=False,
                 fv_threshold_low=0.0, fv_threshold_high=64.0,
                 dynamic_edge=False, mm_enabled=False,
                 name="default"):
        self.min_edge = min_edge
        self.max_position = max_position
        self.spread_limit = spread_limit
        self.min_price = min_price
        self.max_price = max_price
        self.kelly_fraction = kelly_fraction
        self.max_order_qty = max_order_qty
        self.buy_only = buy_only
        self.sell_only = sell_only
        self.favorites_only = favorites_only
        self.underdogs_only = underdogs_only
        self.fv_threshold_low = fv_threshold_low
        self.fv_threshold_high = fv_threshold_high
        self.dynamic_edge = dynamic_edge
        self.mm_enabled = mm_enabled
        self.name = name

        # State
        self.positions = defaultdict(int)
        self.cash = 0.0
        self.trade_count = 0
        self.trade_log = []

    def compute_qty(self, fv, price, position, side):
        if side == "buy":
            edge = fv - price
            if edge <= 0: return 0
            variance = max(fv * (64 - fv), 1.0)
            kelly = edge / variance * 64
            raw = kelly * self.kelly_fraction * 500
            qty = max(1, min(int(raw), self.max_order_qty))
            max_buy = self.max_position - position
            return min(qty, max_buy) if max_buy > 0 else 0
        else:
            edge = price - fv
            if edge <= 0: return 0
            variance = max(fv * (64 - fv), 1.0)
            kelly = edge / variance * 64
            raw = kelly * self.kelly_fraction * 500
            qty = max(1, min(int(raw), self.max_order_qty))
            max_sell = self.max_position + position
            return min(qty, max_sell) if max_sell > 0 else 0

    def run(self, market_data):
        for row in market_data:
            team = row.get('team', '')
            if not team:
                continue

            bid = row['best_bid']
            ask = row['best_ask']
            fv = row['fair_value']
            spread = row['spread']

            if bid <= 0 or ask <= 0 or fv <= 0:
                continue
            if spread > self.spread_limit:
                continue

            # FV range filter
            if fv < self.fv_threshold_low or fv > self.fv_threshold_high:
                continue

            # Favorites/underdogs filter
            if self.favorites_only and fv < 10:
                continue
            if self.underdogs_only and fv >= 10:
                continue

            pos = self.positions[team]

            # Dynamic edge scaling
            req_edge = self.min_edge
            if self.dynamic_edge:
                # Scale edge requirement with FV (higher FV = more variance = need more edge)
                req_edge = max(self.min_edge, fv * 0.05)

            # BUY logic
            if not self.sell_only:
                buy_edge = fv - ask
                if buy_edge > req_edge and ask >= self.min_price and ask <= self.max_price:
                    qty = self.compute_qty(fv, ask, pos, "buy")
                    if qty > 0:
                        self.cash -= qty * ask
                        self.positions[team] += qty
                        self.trade_count += 1
                        self.trade_log.append(('BUY', team, qty, ask, fv, buy_edge))

            # SELL logic
            if not self.buy_only:
                sell_edge = bid - fv
                if sell_edge > req_edge and bid >= self.min_price and bid <= self.max_price:
                    qty = self.compute_qty(fv, bid, pos, "sell")
                    if qty > 0:
                        self.cash += qty * bid
                        self.positions[team] -= qty
                        self.trade_count += 1
                        self.trade_log.append(('SELL', team, qty, bid, fv, sell_edge))

    def get_mtm_pnl(self, latest_fvs):
        mtm = self.cash
        for team, pos in self.positions.items():
            fv = latest_fvs.get(team, 0)
            mtm += pos * fv
        return mtm

    def get_theoretical_edge(self):
        return sum(edge * qty for _, _, qty, _, _, edge in self.trade_log if edge > 0)

    def get_max_loss(self):
        """Worst case: all shorts settle at 64, all longs settle at 0."""
        worst = self.cash
        for team, pos in self.positions.items():
            if pos < 0:
                worst += pos * 64  # short * 64 is very negative
            # longs settle at 0 -> add nothing
        return worst

# Get latest FVs for MTM
latest_fvs = {}
for row in market_data[-200:]:
    team = row.get('team', '')
    if team:
        latest_fvs[team] = row['fair_value']

# --- Define strategies ---
strategies = [
    # Your current strategy
    Backtester(min_edge=1.5, max_position=80, spread_limit=4.0, kelly_fraction=0.15,
               max_order_qty=10, name="CURRENT (your bot)"),

    # More aggressive
    Backtester(min_edge=1.0, max_position=100, spread_limit=5.0, kelly_fraction=0.25,
               max_order_qty=10, name="AGGRESSIVE (edge=1.0, pos=100)"),

    # More conservative
    Backtester(min_edge=2.5, max_position=50, spread_limit=3.0, kelly_fraction=0.10,
               max_order_qty=5, name="CONSERVATIVE (edge=2.5, pos=50)"),

    # High edge only
    Backtester(min_edge=3.0, max_position=80, spread_limit=4.0, kelly_fraction=0.20,
               max_order_qty=10, name="HIGH EDGE ONLY (edge=3.0)"),

    # Sell only (short overpriced)
    Backtester(min_edge=1.5, max_position=80, spread_limit=4.0, kelly_fraction=0.15,
               max_order_qty=10, sell_only=True, name="SELL ONLY"),

    # Buy only (buy underpriced)
    Backtester(min_edge=1.5, max_position=80, spread_limit=4.0, kelly_fraction=0.15,
               max_order_qty=10, buy_only=True, name="BUY ONLY"),

    # Favorites only (FV > 10)
    Backtester(min_edge=1.5, max_position=80, spread_limit=4.0, kelly_fraction=0.15,
               max_order_qty=10, favorites_only=True, name="FAVORITES ONLY (FV>10)"),

    # Underdogs only (FV < 10)
    Backtester(min_edge=1.5, max_position=80, spread_limit=4.0, kelly_fraction=0.15,
               max_order_qty=10, underdogs_only=True, name="UNDERDOGS ONLY (FV<10)"),

    # Tight spread only
    Backtester(min_edge=1.5, max_position=80, spread_limit=1.0, kelly_fraction=0.15,
               max_order_qty=10, name="TIGHT SPREAD (<1.0)"),

    # Dynamic edge scaling
    Backtester(min_edge=1.5, max_position=80, spread_limit=4.0, kelly_fraction=0.15,
               max_order_qty=10, dynamic_edge=True, name="DYNAMIC EDGE"),

    # Small positions, many teams
    Backtester(min_edge=1.0, max_position=30, spread_limit=5.0, kelly_fraction=0.10,
               max_order_qty=5, name="DIVERSIFIED (pos=30, edge=1.0)"),

    # Sniper: high edge, small qty
    Backtester(min_edge=4.0, max_position=50, spread_limit=3.0, kelly_fraction=0.10,
               max_order_qty=3, name="SNIPER (edge=4.0, qty=3)"),

    # Value trap: only trade mid-range FV teams
    Backtester(min_edge=1.5, max_position=80, spread_limit=4.0, kelly_fraction=0.15,
               max_order_qty=10, fv_threshold_low=3.0, fv_threshold_high=15.0,
               name="MID-RANGE ONLY (FV 3-15)"),

    # Whale: max everything
    Backtester(min_edge=0.5, max_position=100, spread_limit=6.0, kelly_fraction=0.30,
               max_order_qty=10, name="WHALE (edge=0.5, kelly=0.3)"),
]

print(f"\nRunning {len(strategies)} strategies against {len(market_data)} market snapshots...\n")

results = []
for strat in strategies:
    strat.run(market_data)
    mtm = strat.get_mtm_pnl(latest_fvs)
    edge = strat.get_theoretical_edge()
    max_loss = strat.get_max_loss()
    n_pos = sum(1 for v in strat.positions.values() if v != 0)

    results.append({
        'name': strat.name,
        'trades': strat.trade_count,
        'mtm_pnl': mtm,
        'edge': edge,
        'cash': strat.cash,
        'max_loss': max_loss,
        'n_positions': n_pos,
        'sharpe_approx': mtm / abs(max_loss) * 10 if max_loss != 0 else 0,
    })

# Sort by MTM P&L
results.sort(key=lambda x: x['mtm_pnl'], reverse=True)

print(f"{'Strategy':<38} {'Trades':>7} {'MTM P&L':>10} {'Edge':>10} {'Cash':>10} {'MaxLoss':>10} {'Pos':>4} {'Sharpe*':>8}")
print("-" * 105)
for r in results:
    marker = " <<<" if "CURRENT" in r['name'] else ""
    print(f"{r['name']:<38} {r['trades']:>7} {r['mtm_pnl']:>10.2f} {r['edge']:>10.2f} {r['cash']:>10.2f} {r['max_loss']:>10.0f} {r['n_positions']:>4} {r['sharpe_approx']:>8.3f}{marker}")

print("\n  * Sharpe approximation = MTM / |MaxLoss| * 10 (higher = better risk-adjusted)")


# ============================================================
# PART 3: PARAMETER OPTIMIZATION (Grid Search)
# ============================================================

print("\n\n" + "=" * 90)
print("PART 3: OPTIMAL STRATEGY SEARCH (Grid Search)")
print("=" * 90)

# Grid search over key parameters
edge_values = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0]
position_values = [30, 50, 80, 100]
kelly_values = [0.05, 0.10, 0.15, 0.20, 0.30]
spread_values = [2.0, 3.0, 4.0, 5.0]

print(f"\nGrid: {len(edge_values)} edges x {len(position_values)} positions x {len(kelly_values)} kellys x {len(spread_values)} spreads = {len(edge_values)*len(position_values)*len(kelly_values)*len(spread_values)} combinations")
print("Running optimization...\n")

best_mtm = None
best_sharpe = None
all_results = []

for min_edge, max_pos, kelly, spread_lim in product(edge_values, position_values, kelly_values, spread_values):
    bt = Backtester(
        min_edge=min_edge, max_position=max_pos, spread_limit=spread_lim,
        kelly_fraction=kelly, max_order_qty=10,
        name=f"e={min_edge},p={max_pos},k={kelly},s={spread_lim}"
    )
    bt.run(market_data)
    mtm = bt.get_mtm_pnl(latest_fvs)
    max_loss = bt.get_max_loss()
    sharpe = mtm / abs(max_loss) * 10 if max_loss != 0 else 0

    result = {
        'min_edge': min_edge, 'max_pos': max_pos, 'kelly': kelly,
        'spread_lim': spread_lim, 'mtm': mtm, 'max_loss': max_loss,
        'sharpe': sharpe, 'trades': bt.trade_count,
    }
    all_results.append(result)

    if best_mtm is None or mtm > best_mtm['mtm']:
        best_mtm = result
    if best_sharpe is None or sharpe > best_sharpe['sharpe']:
        best_sharpe = result

print("TOP 10 BY MTM P&L:")
print(f"{'Edge':>6} {'MaxPos':>7} {'Kelly':>6} {'SpdLim':>7} {'Trades':>7} {'MTM P&L':>10} {'MaxLoss':>10} {'Sharpe':>8}")
print("-" * 72)
for r in sorted(all_results, key=lambda x: x['mtm'], reverse=True)[:10]:
    print(f"{r['min_edge']:>6.1f} {r['max_pos']:>7} {r['kelly']:>6.2f} {r['spread_lim']:>7.1f} {r['trades']:>7} {r['mtm']:>10.2f} {r['max_loss']:>10.0f} {r['sharpe']:>8.3f}")

print("\nTOP 10 BY RISK-ADJUSTED RETURN (Sharpe*):")
print(f"{'Edge':>6} {'MaxPos':>7} {'Kelly':>6} {'SpdLim':>7} {'Trades':>7} {'MTM P&L':>10} {'MaxLoss':>10} {'Sharpe':>8}")
print("-" * 72)
for r in sorted(all_results, key=lambda x: x['sharpe'], reverse=True)[:10]:
    print(f"{r['min_edge']:>6.1f} {r['max_pos']:>7} {r['kelly']:>6.2f} {r['spread_lim']:>7.1f} {r['trades']:>7} {r['mtm']:>10.2f} {r['max_loss']:>10.0f} {r['sharpe']:>8.3f}")

print(f"\n  BEST MTM P&L: edge={best_mtm['min_edge']}, pos={best_mtm['max_pos']}, kelly={best_mtm['kelly']}, spread={best_mtm['spread_lim']}")
print(f"    -> MTM=${best_mtm['mtm']:.2f}, MaxLoss=${best_mtm['max_loss']:.0f}, {best_mtm['trades']} trades")

print(f"\n  BEST RISK-ADJUSTED: edge={best_sharpe['min_edge']}, pos={best_sharpe['max_pos']}, kelly={best_sharpe['kelly']}, spread={best_sharpe['spread_lim']}")
print(f"    -> MTM=${best_sharpe['mtm']:.2f}, MaxLoss=${best_sharpe['max_loss']:.0f}, Sharpe={best_sharpe['sharpe']:.3f}")

# Your current strategy for comparison
your_result = [r for r in all_results if r['min_edge'] == 1.5 and r['max_pos'] == 80 and r['kelly'] == 0.15 and r['spread_lim'] == 4.0]
if your_result:
    yr = your_result[0]
    print(f"\n  YOUR CURRENT: edge=1.5, pos=80, kelly=0.15, spread=4.0")
    print(f"    -> MTM=${yr['mtm']:.2f}, MaxLoss=${yr['max_loss']:.0f}, Sharpe={yr['sharpe']:.3f}")

    improvement_mtm = best_mtm['mtm'] - yr['mtm']
    improvement_sharpe = best_sharpe['sharpe'] - yr['sharpe']
    print(f"\n  Potential MTM improvement: +${improvement_mtm:.2f} ({improvement_mtm/max(yr['mtm'],1)*100:.1f}%)")
    print(f"  Potential Sharpe improvement: +{improvement_sharpe:.3f}")

# --- Parameter sensitivity analysis ---
print("\n--- PARAMETER SENSITIVITY ---")

# Edge sensitivity
print("\n  Edge sensitivity (other params at your current):")
for edge in edge_values:
    matches = [r for r in all_results if r['min_edge'] == edge and r['max_pos'] == 80 and r['kelly'] == 0.15 and r['spread_lim'] == 4.0]
    if matches:
        r = matches[0]
        marker = " <-- current" if edge == 1.5 else ""
        print(f"    edge={edge:.1f}: MTM=${r['mtm']:>9.2f}, trades={r['trades']:>5}, sharpe={r['sharpe']:.3f}{marker}")

# Position limit sensitivity
print("\n  Position limit sensitivity:")
for pos in position_values:
    matches = [r for r in all_results if r['min_edge'] == 1.5 and r['max_pos'] == pos and r['kelly'] == 0.15 and r['spread_lim'] == 4.0]
    if matches:
        r = matches[0]
        marker = " <-- current" if pos == 80 else ""
        print(f"    pos={pos:>3}: MTM=${r['mtm']:>9.2f}, trades={r['trades']:>5}, sharpe={r['sharpe']:.3f}{marker}")

# Kelly sensitivity
print("\n  Kelly fraction sensitivity:")
for k in kelly_values:
    matches = [r for r in all_results if r['min_edge'] == 1.5 and r['max_pos'] == 80 and r['kelly'] == k and r['spread_lim'] == 4.0]
    if matches:
        r = matches[0]
        marker = " <-- current" if k == 0.15 else ""
        print(f"    kelly={k:.2f}: MTM=${r['mtm']:>9.2f}, trades={r['trades']:>5}, sharpe={r['sharpe']:.3f}{marker}")


# ============================================================
# PART 4: TODAY'S GAMES & TRADING RECOMMENDATIONS
# ============================================================

print("\n\n" + "=" * 90)
print("PART 4: TODAY'S GAMES (March 20, 2026) & RECOMMENDATIONS")
print("=" * 90)

# From model.py bracket structure - First round games start March 20-21
# The tournament typically starts with First Four, then R1 on Thu/Fri
# Based on the bracket in model.py:

from model import (
    EAST_BRACKET, WEST_BRACKET, SOUTH_BRACKET, MIDWEST_BRACKET,
    FIRST_FOUR, TEAM_RATINGS, win_probability
)

# March Madness 2026 schedule:
# First Four: March 18-19 (already happened based on trading data from March 19)
# Round 1: March 20-21
# Round 2: March 22-23

print("""
TOURNAMENT SCHEDULE (typical):
  First Four:  March 18-19 (COMPLETED - trading data starts March 19)
  Round of 64: March 20-21 (TODAY!)
  Round of 32: March 22-23
  Sweet 16:    March 27-28
  Elite Eight: March 29-30
  Final Four:  April 4
  Championship: April 6
""")

print("TODAY'S GAMES (Round of 64 - March 20):")
print("=" * 70)

# Round 1 typically has 16 games per day (2 days)
# Day 1 (March 20) usually has East + West regions
# Day 2 (March 21) usually has South + Midwest regions
# But this can vary - let's show all R1 games

all_r1_games = []
for region_name, bracket in [("East", EAST_BRACKET), ("West", WEST_BRACKET),
                               ("South", SOUTH_BRACKET), ("Midwest", MIDWEST_BRACKET)]:
    for m in bracket:
        team_a = m.team_a
        team_b = m.team_b
        # Skip First Four placeholders
        if team_a.startswith("FF_") or team_b.startswith("FF_"):
            continue

        rating_a = TEAM_RATINGS.get(team_a, 0)
        rating_b = TEAM_RATINGS.get(team_b, 0)
        prob_a = win_probability(rating_a, rating_b)

        fv_a = latest_fvs.get(team_a, 0)
        fv_b = latest_fvs.get(team_b, 0)

        mkt_mid_a = team_mispricing.get(team_a, {}).get('avg_mid', 0)
        mkt_mid_b = team_mispricing.get(team_b, {}).get('avg_mid', 0)

        all_r1_games.append({
            'region': region_name,
            'team_a': team_a, 'seed_a': m.seed_a,
            'team_b': team_b, 'seed_b': m.seed_b,
            'prob_a': prob_a, 'rating_a': rating_a, 'rating_b': rating_b,
            'fv_a': fv_a, 'fv_b': fv_b,
            'mkt_a': mkt_mid_a, 'mkt_b': mkt_mid_b,
        })

# Show Day 1 games (East + West typically)
for day_label, regions in [("DAY 1 (likely today)", ["East", "West"]),
                            ("DAY 2 (likely tomorrow)", ["South", "Midwest"])]:
    print(f"\n  {day_label}:")
    print(f"  {'Region':<8} {'#':>2} {'Team A':<20} {'vs':>3} {'Team B':<20} {'P(A)':>6} {'FV_A':>6} {'FV_B':>6} {'Mkt_A':>7} {'Mkt_B':>7}")
    print("  " + "-" * 90)

    for g in all_r1_games:
        if g['region'] not in regions:
            continue
        print(f"  {g['region']:<8} {g['seed_a']:>2} {g['team_a']:<20} {'vs':>3} {g['team_b']:<20} "
              f"{g['prob_a']:>5.1%} {g['fv_a']:>6.2f} {g['fv_b']:>6.2f} {g['mkt_a']:>7.2f} {g['mkt_b']:>7.2f}")

# Trading recommendations for today's games
print("\n\nTRADING RECOMMENDATIONS FOR TODAY'S GAMES:")
print("=" * 70)

from collections import namedtuple

positions_dict = defaultdict(int)
for t in load_trades():
    if t['side'] == 'BUY':
        positions_dict[t['team']] += t['qty']
    else:
        positions_dict[t['team']] -= t['qty']

recommendations = []

for g in all_r1_games:
    for team_key, fv_key, mkt_key, seed_key in [
        ('team_a', 'fv_a', 'mkt_a', 'seed_a'),
        ('team_b', 'fv_b', 'mkt_b', 'seed_b')
    ]:
        team = g[team_key]
        fv = g[fv_key]
        mkt = g[mkt_key]
        seed = g[seed_key]
        pos = positions_dict.get(team, 0)

        if mkt <= 0:
            continue

        edge = mkt - fv if mkt > fv else fv - mkt
        direction = "SELL" if mkt > fv else "BUY"

        # Only recommend if meaningful edge
        if edge > 1.0:
            # Risk context
            risk_note = ""
            if pos < -50:
                risk_note = "[CAUTION: already max short]"
            elif pos > 40:
                risk_note = "[CAUTION: already heavily long]"

            volatility_note = ""
            if g['region'] in ["East", "West"]:  # playing today
                volatility_note = "[GAME TODAY - high vol expected]"

            recommendations.append({
                'team': team, 'direction': direction, 'edge': edge,
                'fv': fv, 'mkt': mkt, 'pos': pos, 'seed': seed,
                'region': g['region'], 'risk_note': risk_note,
                'vol_note': volatility_note,
            })

recommendations.sort(key=lambda x: x['edge'], reverse=True)

print(f"\n{'Team':<22} {'Action':>6} {'Edge':>6} {'FV':>7} {'Market':>8} {'CurPos':>7} {'Seed':>5} Notes")
print("-" * 90)
for r in recommendations[:20]:
    notes = f"{r['risk_note']} {r['vol_note']}".strip()
    print(f"{r['team']:<22} {r['direction']:>6} {r['edge']:>6.2f} {r['fv']:>7.2f} {r['mkt']:>8.2f} {r['pos']:>7} {r['seed']:>5} {notes}")

# Should we focus only on today's games?
print("""
\nSHOULD YOU ONLY TRADE TODAY'S GAMES?
-------------------------------------
NO - but today's games deserve EXTRA ATTENTION. Here's why:

1. VOLATILITY SPIKE: Teams playing today will have massive FV swings as the
   game progresses. A 1-seed losing at halftime could drop from FV=35 to FV=20.
   This creates the BIGGEST edges of the tournament.

2. ELIMINATION EVENTS: When a team loses, their contract settles at 0.
   If you're short a team that gets eliminated, you pocket the full sell price.
   If you're long and they lose, you take a total loss.

3. LIVE GAME ADJUSTMENTS: The bot already adjusts FV during live games via
   live_win_probability(). This is when those adjustments matter most.

4. BUT DON'T IGNORE NON-PLAYING TEAMS: Other teams' FVs also shift when
   bracket opponents get eliminated. E.g., if Duke's potential opponent
   gets upset, Duke's FV increases slightly.

RECOMMENDATION:
  - INCREASE monitoring frequency during today's games
  - Consider WIDENING position limits for today's teams (+20%)
  - TIGHTEN spread limits during games (liquidity improves during games)
  - Watch for PANIC SELLS when a favorite is down at halftime -> BUY opportunity
  - Your SHORT positions in low-seed teams playing today are your BEST bets
""")

print("\nYOUR POSITIONS IN TEAMS PLAYING TODAY (East + West):")
print("-" * 50)
today_teams = set()
for g in all_r1_games:
    if g['region'] in ["East", "West"]:
        today_teams.add(g['team_a'])
        today_teams.add(g['team_b'])

for team in sorted(today_teams):
    pos = positions_dict.get(team, 0)
    fv = latest_fvs.get(team, 0)
    if pos != 0:
        action = "Will profit if eliminated" if pos < 0 else "RISK: loses value if eliminated"
        print(f"  {team:<22} pos={pos:>5} FV={fv:>6.2f}  -> {action}")
    else:
        print(f"  {team:<22} pos={pos:>5} FV={fv:>6.2f}  -> no position")


print("\n\n" + "=" * 90)
print("ANALYSIS COMPLETE")
print("=" * 90)
