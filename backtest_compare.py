"""
Backtest comparison: OLD strategy vs NEW strategy (with Gemini improvements)
Replays market_data.csv through both strategies and compares performance.
"""

import csv
import math
from collections import defaultdict

# ===== SHARED PARAMETERS =====
MIN_EDGE = 1.5
MAX_POSITION = 80
KELLY_FRACTION = 0.15
MAX_ORDER_QTY = 10
SPREAD_LIMIT = 3.0
MIN_PRICE = 6.4    # 10% of 64
MAX_PRICE = 57.6   # 90% of 64

# ===== NEW STRATEGY PARAMETERS =====
RISK_AVERSION_GAMMA = 0.05
WASH_TRADE_SPREAD = 0.05
WASH_TRADE_FV_DIST = 2.0


def compute_order_qty(fair_value, price, position, side):
    """Kelly-based sizing (same for both strategies)."""
    if side == "buy":
        edge = fair_value - price
        if edge <= 0:
            return 0
        variance = max(fair_value * (64 - fair_value), 1.0)
        kelly_fraction = edge / variance * 64
        raw_qty = kelly_fraction * KELLY_FRACTION * 500
        qty = max(1, min(int(raw_qty), MAX_ORDER_QTY))
        max_buy = MAX_POSITION - position
        return min(qty, max_buy) if max_buy > 0 else 0
    else:
        edge = price - fair_value
        if edge <= 0:
            return 0
        variance = max(fair_value * (64 - fair_value), 1.0)
        kelly_fraction = edge / variance * 64
        raw_qty = kelly_fraction * KELLY_FRACTION * 500
        qty = max(1, min(int(raw_qty), MAX_ORDER_QTY))
        max_sell = MAX_POSITION + position
        return min(qty, max_sell) if max_sell > 0 else 0


class Strategy:
    def __init__(self, name, use_wash_detect=False, use_as_skew=False, use_new_ov=False, asymmetric_edge=False, ov_live_only=False):
        self.name = name
        self.positions = defaultdict(int)
        self.cash = 0.0
        self.trades = []
        self.use_wash_detect = use_wash_detect
        self.use_as_skew = use_as_skew
        self.use_new_ov = use_new_ov
        self.asymmetric_edge = asymmetric_edge
        self.ov_live_only = ov_live_only
        self.wash_skips = 0
        self.total_edge = 0.0

    def evaluate(self, symbol, best_bid, best_ask, fair_value, spread, timestamp):
        """Evaluate a single market tick and potentially trade."""
        if fair_value <= 0.01:
            return

        # Wash trade detection (NEW only)
        if self.use_wash_detect and spread <= WASH_TRADE_SPREAD and spread > 0:
            market_mid = (best_bid + best_ask) / 2.0
            if abs(market_mid - fair_value) > WASH_TRADE_FV_DIST:
                self.wash_skips += 1
                return

        # Spread constraint
        if spread > SPREAD_LIMIT and best_bid > 0 and best_ask < 64:
            return

        position = self.positions[symbol]

        # A-S inventory skewing (NEW only)
        if self.use_as_skew:
            skewed_fv = fair_value - position * RISK_AVERSION_GAMMA
        else:
            skewed_fv = fair_value

        # Option Value calculation
        p = min(max(fair_value / 64.0, 0.0), 1.0)
        time_rem = 2400.0  # pre-game (Day 1 was pre-tournament)

        # If OV live only, skip OV when pre-game (time_rem == 2400 means no live game)
        if self.ov_live_only and time_rem >= 2400.0:
            option_value = 0.0
        elif self.use_new_ov:
            N = time_rem / 120.0
            if N > 0:
                option_value = 0.39 * math.pow(N, 0.42) * (1.0 + 16.12 * p * (1.0 - p))
            else:
                option_value = 0.0
        else:
            # Old OV formula
            option_value = p * (1.0 - p) * (time_rem / 2400.0) * 6.4

        # === BUY ===
        if best_ask > 0:
            buy_edge = skewed_fv - best_ask

            if self.asymmetric_edge:
                # NEW: closing short needs only MIN_EDGE, opening long needs MIN_EDGE + OV
                effective_req = MIN_EDGE if position < 0 else MIN_EDGE + option_value
            else:
                # OLD: always MIN_EDGE (OV was used differently - added to sell threshold for longs)
                effective_req = MIN_EDGE

            if buy_edge > effective_req and MIN_PRICE <= best_ask <= MAX_PRICE:
                qty = compute_order_qty(fair_value, best_ask, position, "buy")
                if qty > 0:
                    cost = qty * best_ask
                    self.cash -= cost
                    self.positions[symbol] += qty
                    edge = fair_value - best_ask
                    self.total_edge += edge * qty
                    self.trades.append((timestamp, symbol, "BUY", qty, best_ask, fair_value, edge))

        # === SELL ===
        if best_bid > 0:
            sell_edge = best_bid - skewed_fv

            if self.asymmetric_edge:
                # NEW: closing long needs only MIN_EDGE, opening short needs MIN_EDGE + OV
                effective_req = MIN_EDGE if position > 0 else MIN_EDGE + option_value
            else:
                # OLD: always MIN_EDGE
                effective_req = MIN_EDGE

            if sell_edge > effective_req and MIN_PRICE <= best_bid <= MAX_PRICE:
                qty = compute_order_qty(fair_value, best_bid, position, "sell")
                if qty > 0:
                    revenue = qty * best_bid
                    self.cash += revenue
                    self.positions[symbol] -= qty
                    edge = best_bid - fair_value
                    self.total_edge += edge * qty
                    self.trades.append((timestamp, symbol, "SELL", qty, best_bid, fair_value, edge))



from model import compute_fair_values, SYMBOL_TO_TEAM

def run_backtest():
    print("Pre-computing NEW initial fair values from updated model (50,000 simulations)...")
    from model import TEAM_TO_SYMBOL
    new_fvs_baseline = compute_fair_values(n_sims=50000)
    
    # Map the model output to Symbols
    new_fvs_symbol = {}
    for team, fv in new_fvs_baseline.items():
        symbol = TEAM_TO_SYMBOL.get(team, team)
        new_fvs_symbol[symbol] = fv
        
    print("Done computing! Running backtest with old vs new valuations...")

    # OLD strategy: uses the old CSV fair values
    old = Strategy("OLD (CSV Valuations)", use_wash_detect=False, use_as_skew=False, use_new_ov=False, asymmetric_edge=False)

    # NEW strategy: uses the NEW Model valuations
    new_vals_only = Strategy("NEW INITIAL RATINGS (No Trading Upgrades)", use_wash_detect=False, use_as_skew=False, use_new_ov=False, asymmetric_edge=False)
    
    # NEW BEST: uses the NEW Model valuations + Gemini upgrades
    new_best = Strategy("NEW RATINGS + NEW TRADING UPGRADES", use_wash_detect=True, use_as_skew=True, use_new_ov=True, asymmetric_edge=True)

    strategies = [old, new_vals_only, new_best]

    # Read market data
    print("Reading market_data.csv...")
    tick_count = 0
    last_fvs = {}  # track latest FV per symbol

    with open("market_data.csv", "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            symbol = row.get("symbol", "")
            try:
                best_bid = float(row["best_bid"]) if row.get("best_bid") else 0.0
                best_ask = float(row["best_ask"]) if row.get("best_ask") else 0.0
                fair_value = float(row["fair_value"]) if row.get("fair_value") else 0.0
                spread = float(row["spread"]) if row.get("spread") else 0.0
            except (ValueError, KeyError):
                continue

            if fair_value <= 0 or best_bid <= 0 or best_ask <= 0:
                continue

            timestamp = row.get("timestamp", "")
            last_fvs[symbol] = fair_value

            old.evaluate(symbol, best_bid, best_ask, fair_value, spread, timestamp)

            # For the New Strategies, inject the freshly minted pregame fair values!
            new_fair_value = new_fvs_symbol.get(symbol, fair_value) # Fallback to CSV if missing
            new_vals_only.evaluate(symbol, best_bid, best_ask, new_fair_value, spread, timestamp)
            new_best.evaluate(symbol, best_bid, best_ask, new_fair_value, spread, timestamp)

            tick_count += 1

    print(f"Processed {tick_count:,} market ticks\n")

    # ===== RESULTS =====
    print("=" * 100)
    print(f"{'BACKTEST COMPARISON':^100}")
    print("=" * 100)

    for strat in strategies:
        print(f"\n{'--- ' + strat.name + ' ---':^100}")

        # Compute MTM P&L
        mtm = strat.cash
        long_exposure = 0
        short_exposure = 0
        max_short_loss = 0

        pos_list = []
        for sym, qty in sorted(strat.positions.items(), key=lambda x: abs(x[1]), reverse=True):
            if qty == 0:
                continue
            fv = last_fvs.get(sym, 0)
            mtm += qty * fv
            if qty > 0:
                long_exposure += qty * fv
            else:
                short_exposure += abs(qty) * fv
                max_short_loss += abs(qty) * 64  # worst case: team wins championship
            pos_list.append((sym, qty, fv))

        # Trade stats
        buy_trades = [t for t in strat.trades if t[2] == "BUY"]
        sell_trades = [t for t in strat.trades if t[2] == "SELL"]
        buy_qty = sum(t[3] for t in buy_trades)
        sell_qty = sum(t[3] for t in sell_trades)
        unique_teams_bought = len(set(t[1] for t in buy_trades))
        unique_teams_sold = len(set(t[1] for t in sell_trades))

        print(f"  Total Trades:        {len(strat.trades):>6}")
        print(f"  Buys:                {len(buy_trades):>6}  ({buy_qty} contracts, {unique_teams_bought} teams)")
        print(f"  Sells:               {len(sell_trades):>6}  ({sell_qty} contracts, {unique_teams_sold} teams)")
        print(f"  Net Cash:           ${strat.cash:>10,.2f}")
        print(f"  Mark-to-Market P&L: ${mtm:>10,.2f}")
        print(f"  Total Edge Captured:${strat.total_edge:>10,.2f}")
        print(f"  Long Exposure:      ${long_exposure:>10,.2f}")
        print(f"  Short Exposure:     ${short_exposure:>10,.2f}")
        print(f"  Max Short Loss:     ${max_short_loss:>10,.2f}")
        if max_short_loss > 0:
            print(f"  Risk-Adj (MTM/Max): {mtm / max_short_loss * 100:>9.2f}%")
        if strat.wash_skips > 0:
            print(f"  Wash Trade Skips:    {strat.wash_skips:>5}")

        # Top positions
        print(f"\n  Top 10 Positions:")
        print(f"    {'Symbol':<20} {'Pos':>5} {'Dir':<6} {'FV':>7} {'Unrealized':>10}")
        for sym, qty, fv in pos_list[:10]:
            direction = "LONG" if qty > 0 else "SHORT"
            # For longs: unrealized = qty * (fv - avg_buy_price) ... approximate with qty*fv - cost
            # Simplified: just show position * fv as position value
            pnl_approx = qty * fv  # not including cash, just exposure
            print(f"    {sym:<20} {qty:>5} {direction:<6} {fv:>7.2f} {pnl_approx:>10.2f}")

    # ===== HEAD-TO-HEAD COMPARISON =====
    print("\n" + "=" * 100)
    print(f"{'HEAD-TO-HEAD: OLD vs NEW':^100}")
    print("=" * 100)

    old_mtm = old.cash + sum(qty * last_fvs.get(sym, 0) for sym, qty in old.positions.items())
    new_mtm = new_best.cash + sum(qty * last_fvs.get(sym, 0) for sym, qty in new_best.positions.items())

    old_max_loss = sum(abs(qty) * 64 for sym, qty in old.positions.items() if qty < 0)
    new_max_loss = sum(abs(qty) * 64 for sym, qty in new_best.positions.items() if qty < 0)

    print(f"\n  {'Metric':<30} {'OLD':>15} {'NEW':>15} {'Delta':>15}")
    print(f"  {'-'*75}")
    print(f"  {'Trades':<30} {len(old.trades):>15} {len(new_best.trades):>15} {len(new_best.trades)-len(old.trades):>+15}")
    print(f"  {'MTM P&L':<30} ${old_mtm:>14,.2f} ${new_mtm:>14,.2f} ${new_mtm-old_mtm:>+14,.2f}")
    print(f"  {'Edge Captured':<30} ${old.total_edge:>14,.2f} ${new_best.total_edge:>14,.2f} ${new_best.total_edge-old.total_edge:>+14,.2f}")
    print(f"  {'Max Short Loss':<30} ${old_max_loss:>14,.2f} ${new_max_loss:>14,.2f} ${new_max_loss-old_max_loss:>+14,.2f}")

    if old_max_loss > 0 and new_max_loss > 0:
        old_ra = old_mtm / old_max_loss * 100
        new_ra = new_mtm / new_max_loss * 100
        print(f"  {'Risk-Adj Return':<30} {old_ra:>14.2f}% {new_ra:>14.2f}% {new_ra-old_ra:>+14.2f}%")

    print(f"  {'Wash Trade Skips':<30} {'N/A':>15} {new_best.wash_skips:>15}")

    # Show trades unique to NEW but not in OLD (and vice versa)
    old_teams_traded = set(t[1] for t in old.trades)
    new_teams_traded = set(t[1] for t in new_best.trades)
    only_old = old_teams_traded - new_teams_traded
    only_new = new_teams_traded - old_teams_traded

    if only_old:
        print(f"\n  Teams traded by OLD only: {', '.join(sorted(only_old))}")
    if only_new:
        print(f"  Teams traded by NEW only: {', '.join(sorted(only_new))}")

    # Per-team comparison for shared teams
    print(f"\n  Per-Team Position Comparison (top differences):")
    print(f"    {'Symbol':<20} {'OLD Pos':>8} {'NEW Pos':>8} {'Diff':>8} {'OLD MTM':>10} {'NEW MTM':>10}")
    all_symbols = set(list(old.positions.keys()) + list(new_best.positions.keys()))
    diffs = []
    for sym in all_symbols:
        old_pos = old.positions.get(sym, 0)
        new_pos = new_best.positions.get(sym, 0)
        if old_pos == 0 and new_pos == 0:
            continue
        fv = last_fvs.get(sym, 0)
        old_val = old_pos * fv
        new_val = new_pos * fv
        diffs.append((sym, old_pos, new_pos, new_pos - old_pos, old_val, new_val))

    diffs.sort(key=lambda x: abs(x[3]), reverse=True)
    for sym, op, np_, d, ov, nv in diffs[:15]:
        print(f"    {sym:<20} {op:>8} {np_:>8} {d:>+8} ${ov:>9.2f} ${nv:>9.2f}")

    print("\n" + "=" * 100)


if __name__ == "__main__":
    run_backtest()
