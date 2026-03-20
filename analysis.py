"""
NCAA March Madness Trading Analysis
Comprehensive analysis of trades, market data, positions, and P&L.
"""

import csv
from collections import defaultdict
from datetime import datetime
import statistics
import math

# ============================================================
# 1. LOAD DATA
# ============================================================

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

def load_market_data(filename="market_data.csv", sample_every=50):
    """Load market data, sampling every Nth row to keep memory reasonable."""
    data = []
    count = 0
    with open(filename, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            count += 1
            if count % sample_every == 0 or count <= 200:
                try:
                    row['best_bid'] = float(row['best_bid']) if row.get('best_bid') else 0
                    row['best_ask'] = float(row['best_ask']) if row.get('best_ask') else 0
                    row['fair_value'] = float(row['fair_value'])
                    row['spread'] = float(row['spread']) if row.get('spread') else 0
                    data.append(row)
                except (ValueError, KeyError):
                    continue
    return data, count

# Settlement structure per round
SETTLEMENT = {
    "R1_EXIT": 0,       # Lost in first round
    "R2_EXIT": 2,       # Lost in second round
    "S16_EXIT": 4,      # Lost in Sweet 16
    "E8_EXIT": 8,       # Lost in Elite Eight
    "F4_EXIT": 16,      # Lost in Final Four
    "RUNNER_UP": 32,    # Lost in Championship
    "CHAMPION": 64,     # Won Championship
}

print("=" * 80)
print("NCAA MARCH MADNESS TRADING ANALYSIS")
print("=" * 80)

trades = load_trades()
market_data, total_market_rows = load_market_data()

print(f"\nLoaded {len(trades)} trades")
print(f"Loaded {total_market_rows} market data rows (sampled {len(market_data)})")

# ============================================================
# 2. POSITION & P&L ANALYSIS
# ============================================================

print("\n" + "=" * 80)
print("POSITION & P&L ANALYSIS")
print("=" * 80)

positions = defaultdict(int)       # team -> net qty
cost_basis = defaultdict(float)    # team -> total cost (signed)
trade_count_by_team = defaultdict(int)
volume_by_team = defaultdict(int)
total_edge_captured = defaultdict(float)

for t in trades:
    team = t['team']
    qty = t['qty']
    price = t['price']
    side = t['side']
    edge = t['edge']

    trade_count_by_team[team] += 1
    volume_by_team[team] += qty
    total_edge_captured[team] += edge * qty

    if side == 'BUY':
        positions[team] += qty
        cost_basis[team] -= qty * price  # cash out
    elif side == 'SELL':
        positions[team] -= qty
        cost_basis[team] += qty * price  # cash in

# Get latest fair values from market data
latest_fv = {}
for row in market_data:
    team = row.get('team', '')
    if team:
        latest_fv[team] = row['fair_value']

# Also get latest fair values from the last trades
for t in trades:
    team = t['team']
    if t['fair_value'] > 0:
        latest_fv[team] = t['fair_value']

print(f"\n{'Team':<25} {'Position':>8} {'Cash Flow':>10} {'Last FV':>8} {'MTM P&L':>10} {'Trades':>7} {'Volume':>7} {'Edge$':>8}")
print("-" * 100)

total_cash = 0
total_mtm = 0
total_theoretical_edge = 0

sorted_positions = sorted(positions.items(), key=lambda x: abs(x[1]), reverse=True)

for team, pos in sorted_positions:
    if pos == 0:
        continue
    cash = cost_basis[team]
    fv = latest_fv.get(team, 0)
    mtm = cash + pos * fv  # unrealized P&L
    trades_n = trade_count_by_team[team]
    vol = volume_by_team[team]
    edge_dollars = total_edge_captured[team]

    total_cash += cash
    total_mtm += mtm
    total_theoretical_edge += edge_dollars

    print(f"{team:<25} {pos:>8} {cash:>10.2f} {fv:>8.2f} {mtm:>10.2f} {trades_n:>7} {vol:>7} {edge_dollars:>8.2f}")

print("-" * 100)
print(f"{'TOTAL':<25} {'':>8} {total_cash:>10.2f} {'':>8} {total_mtm:>10.2f} {len(trades):>7} {sum(volume_by_team.values()):>7} {total_theoretical_edge:>8.2f}")

# ============================================================
# 3. TRADE DIRECTION ANALYSIS
# ============================================================

print("\n" + "=" * 80)
print("TRADE DIRECTION BREAKDOWN")
print("=" * 80)

buys = [t for t in trades if t['side'] == 'BUY']
sells = [t for t in trades if t['side'] == 'SELL']

print(f"\nTotal BUY trades:  {len(buys)} ({sum(t['qty'] for t in buys)} contracts)")
print(f"Total SELL trades: {len(sells)} ({sum(t['qty'] for t in sells)} contracts)")

buy_teams = set(t['team'] for t in buys)
sell_teams = set(t['team'] for t in sells)

print(f"\nTeams BOUGHT: {sorted(buy_teams)}")
print(f"Teams SOLD:   {sorted(sell_teams)}")

# ============================================================
# 4. BAD TRADES ANALYSIS (fair_value = 0 or negative edge)
# ============================================================

print("\n" + "=" * 80)
print("BAD TRADES (fair_value=0 or negative edge)")
print("=" * 80)

bad_trades = [t for t in trades if t['fair_value'] == 0 or t['edge'] < 0]
if bad_trades:
    print(f"\nFound {len(bad_trades)} problematic trades:")
    for t in bad_trades:
        print(f"  {t['timestamp']} | {t['side']} {t['qty']}x {t['team']} @ {t['price']} | FV={t['fair_value']} | edge={t['edge']}")

    bad_cost = sum(t['qty'] * t['price'] * (1 if t['side'] == 'BUY' else -1) for t in bad_trades)
    print(f"\n  Total cash impact of bad trades: ${bad_cost:.2f}")
else:
    print("\nNo bad trades found.")

# ============================================================
# 5. EDGE DISTRIBUTION ANALYSIS
# ============================================================

print("\n" + "=" * 80)
print("EDGE STATISTICS")
print("=" * 80)

edges = [t['edge'] for t in trades if t['edge'] > 0]
if edges:
    print(f"\nEdge on good trades:")
    print(f"  Mean edge:   {statistics.mean(edges):.2f}")
    print(f"  Median edge: {statistics.median(edges):.2f}")
    print(f"  Min edge:    {min(edges):.2f}")
    print(f"  Max edge:    {max(edges):.2f}")
    print(f"  Std dev:     {statistics.stdev(edges):.2f}")

    # Edge distribution
    buckets = defaultdict(int)
    for e in edges:
        bucket = int(e)
        buckets[bucket] += 1

    print(f"\n  Edge distribution:")
    for b in sorted(buckets.keys()):
        bar = "#" * (buckets[b] // 2)
        print(f"    {b:>2}-{b+1:<2}: {buckets[b]:>4} trades {bar}")

# Dollar-weighted edge
dollar_edges = [t['edge'] * t['qty'] for t in trades if t['edge'] > 0]
print(f"\n  Total theoretical edge captured: ${sum(dollar_edges):.2f}")
print(f"  Dollar-weighted avg edge: ${sum(dollar_edges)/sum(t['qty'] for t in trades if t['edge'] > 0):.2f} per contract")

# ============================================================
# 6. TEAMS WITH ZERO/NEAR-ZERO FAIR VALUE (LIKELY BUSTED)
# ============================================================

print("\n" + "=" * 80)
print("BUSTED TEAMS ANALYSIS (FV < 1.0 = likely eliminated)")
print("=" * 80)

# Get all teams and their latest fair values
all_team_fv = {}
for row in market_data:
    team = row.get('team', '')
    if team:
        all_team_fv[team] = row['fair_value']

busted_teams = {t: fv for t, fv in all_team_fv.items() if fv < 1.0}
print(f"\nTeams with FV < 1.0 (likely eliminated or very low seeds):")
for team, fv in sorted(busted_teams.items(), key=lambda x: x[1]):
    pos = positions.get(team, 0)
    status = ""
    if pos != 0:
        status = f" ** HOLDING {pos} contracts **"
    print(f"  {team:<25} FV={fv:>6.2f}{status}")

# Check if we hold positions in teams with FV = 0 (definitely busted)
print(f"\n** STOCKS OF BUSTED TEAMS STILL HELD: **")
found_busted_held = False
for team, pos in positions.items():
    if pos != 0:
        fv = latest_fv.get(team, all_team_fv.get(team, -1))
        if fv < 1.0:
            found_busted_held = True
            exposure = pos * fv
            print(f"  {team}: position={pos}, FV={fv:.2f}, exposure=${exposure:.2f}")

if not found_busted_held:
    print("  None - no busted team positions held.")

# ============================================================
# 7. SETTLEMENT STRUCTURE CHECK
# ============================================================

print("\n" + "=" * 80)
print("SETTLEMENT STRUCTURE CHECK")
print("=" * 80)
print("""
The settlement structure in the bot (model.py) is:
  Champion:    64 points
  Runner-up:   32 points
  Final Four:  16 points
  Elite Eight:  8 points
  Sweet 16:     4 points
  Round of 32:  2 points
  Round of 64:  0 points (First Round exit)

Example given by user:
  Duke lost R1 -> settles at 0  [OK] (model has R1_EXIT = 0)
  Illinois to Final Four -> settles at 16  [OK] (model has F4_EXIT = 16)
  Michigan loses in Finals -> settles at 32  [OK] (model has RUNNER_UP = 32)
  Michigan State wins tournament -> settles at 64...

WAIT - the user's example says Michigan State = 6, not 64!
""")

# Re-read the user's example more carefully
print("""
USER'S EXAMPLE:
  Duke = 0 (lost R1)
  Illinois = 16 (Final Four)
  Michigan = 32 (runner-up/lost in finals)
  Michigan State = 6 (beat Michigan in the finals = CHAMPION?)

If Michigan State WINS the tournament and settles at 6... that's NOT our model!
Our model says Champion = 64.

POSSIBLE INTERPRETATION: The "6" might refer to the NUMBER OF WINS:
  - Champion wins 6 games -> settles at 6
  - Runner-up wins 5 games -> but Michigan = 32 doesn't match 5

ALTERNATIVE: Michigan = 32, Michigan St = 6...
  Maybe this is a DIFFERENT settlement rule like "points per win * wins"?
  Or the example might have a typo (6 vs 64)?

Let's check: If settlements are 0, 16, 32, 6:
  - 0 for R1 loss makes sense
  - 16 for F4 could mean "final four exit" (lost semifinal)
  - 32 for "lost in finals" (runner-up)
  - 6 for champion... this is very unusual

IF the settlement structure is actually different from what's coded:
  Our bot uses: 0, 2, 4, 8, 16, 32, 64 (from model.py SETTLEMENT dict)

The "6" for Michigan State (champion) does NOT match 64.
This could be a CRITICAL discrepancy if the actual game uses different settlements.
""")

# ============================================================
# 8. TIMING ANALYSIS
# ============================================================

print("\n" + "=" * 80)
print("TIMING ANALYSIS")
print("=" * 80)

timestamps = [t['timestamp'] for t in trades]
if timestamps:
    first = timestamps[0]
    last = timestamps[-1]
    print(f"\nFirst trade: {first}")
    print(f"Last trade:  {last}")

    # Trades per hour
    hour_counts = defaultdict(int)
    for t in trades:
        ts = t['timestamp']
        try:
            dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            hour_key = dt.strftime("%Y-%m-%d %H:00")
            hour_counts[hour_key] += 1
        except:
            pass

    print(f"\nTrades per hour:")
    for hour in sorted(hour_counts.keys()):
        bar = "#" * (hour_counts[hour] // 3)
        print(f"  {hour}: {hour_counts[hour]:>4} trades {bar}")

# ============================================================
# 9. CONCENTRATION RISK
# ============================================================

print("\n" + "=" * 80)
print("CONCENTRATION / RISK ANALYSIS")
print("=" * 80)

# Top positions by absolute size
print(f"\nTop 10 positions by absolute size:")
sorted_abs = sorted(positions.items(), key=lambda x: abs(x[1]), reverse=True)
for team, pos in sorted_abs[:10]:
    fv = latest_fv.get(team, 0)
    exposure = abs(pos * fv)
    max_loss = abs(pos) * 64 if pos < 0 else abs(pos) * fv  # shorts can go to 64
    print(f"  {team:<25} pos={pos:>5} | FV={fv:>6.2f} | Exposure=${exposure:>8.2f} | Max loss=${max_loss:>8.2f}")

# Total exposure
total_long_exposure = sum(pos * latest_fv.get(team, 0) for team, pos in positions.items() if pos > 0)
total_short_exposure = sum(abs(pos) * latest_fv.get(team, 0) for team, pos in positions.items() if pos < 0)
total_short_max_loss = sum(abs(pos) * 64 for team, pos in positions.items() if pos < 0)

print(f"\nTotal long exposure (pos * FV):  ${total_long_exposure:>10.2f}")
print(f"Total short exposure (|pos| * FV): ${total_short_exposure:>10.2f}")
print(f"Total short max loss (|pos| * 64): ${total_short_max_loss:>10.2f}")

# ============================================================
# 10. MISSED OPPORTUNITIES
# ============================================================

print("\n" + "=" * 80)
print("MISSED OPPORTUNITIES ANALYSIS")
print("=" * 80)

# Check market data for large edges that weren't traded
# Compare traded teams vs all teams in market data
all_market_teams = set()
team_max_edge = defaultdict(float)

for row in market_data:
    team = row.get('team', '')
    if not team:
        continue
    all_market_teams.add(team)

    fv = row['fair_value']
    bid = row['best_bid']
    ask = row['best_ask']
    spread = row['spread']

    # Only consider tradeable opportunities (spread < 4)
    if spread > 4:
        continue

    # Buy edge
    if fv > ask and ask > 0:
        buy_edge = fv - ask
        team_max_edge[team] = max(team_max_edge.get(team, 0), buy_edge)

    # Sell edge
    if bid > fv and bid > 0:
        sell_edge = bid - fv
        team_max_edge[team] = max(team_max_edge.get(team, 0), sell_edge)

traded_teams = set(t['team'] for t in trades)
untraded_with_edge = {t: e for t, e in team_max_edge.items()
                      if t not in traded_teams and e > 1.5}

if untraded_with_edge:
    print(f"\nTeams with edge > 1.5 that were NEVER traded:")
    for team, edge in sorted(untraded_with_edge.items(), key=lambda x: x[1], reverse=True):
        fv = all_team_fv.get(team, 0)
        print(f"  {team:<25} max_edge={edge:>6.2f} FV={fv:>6.2f}")
else:
    print("\nNo significant missed opportunities found in sampled market data.")

# Check for teams that had persistent large edges
print(f"\nTeams with LARGEST edges in market data (including traded):")
for team, edge in sorted(team_max_edge.items(), key=lambda x: x[1], reverse=True)[:15]:
    was_traded = "TRADED" if team in traded_teams else "NOT TRADED"
    fv = all_team_fv.get(team, 0)
    print(f"  {team:<25} max_edge={edge:>6.2f} FV={fv:>6.2f} [{was_traded}]")

# ============================================================
# 11. SELL BIAS ANALYSIS
# ============================================================

print("\n" + "=" * 80)
print("SELL BIAS CHECK")
print("=" * 80)

buy_dollar_vol = sum(t['qty'] * t['price'] for t in trades if t['side'] == 'BUY')
sell_dollar_vol = sum(t['qty'] * t['price'] for t in trades if t['side'] == 'SELL')

print(f"\nBuy dollar volume:  ${buy_dollar_vol:>10.2f}")
print(f"Sell dollar volume: ${sell_dollar_vol:>10.2f}")
print(f"Net:                ${buy_dollar_vol - sell_dollar_vol:>10.2f} ({'net buyer' if buy_dollar_vol > sell_dollar_vol else 'net seller'})")

buy_edge_total = sum(t['edge'] * t['qty'] for t in trades if t['side'] == 'BUY' and t['edge'] > 0)
sell_edge_total = sum(t['edge'] * t['qty'] for t in trades if t['side'] == 'SELL' and t['edge'] > 0)

print(f"\nBuy edge captured:  ${buy_edge_total:>10.2f}")
print(f"Sell edge captured: ${sell_edge_total:>10.2f}")

# ============================================================
# 12. SCENARIO ANALYSIS
# ============================================================

print("\n" + "=" * 80)
print("SCENARIO ANALYSIS - SETTLEMENT P&L")
print("=" * 80)

def calc_settlement_pnl(positions, cost_basis, settlement_values):
    """Calculate P&L given settlement values for each team."""
    total_pnl = 0
    for team, pos in positions.items():
        if pos == 0:
            continue
        settlement = settlement_values.get(team, 0)
        pnl = cost_basis[team] + pos * settlement
        total_pnl += pnl
    return total_pnl

# Scenario 1: All teams settle at current fair value
scenario_fv = {}
for team in positions:
    scenario_fv[team] = latest_fv.get(team, 0)
pnl_fv = calc_settlement_pnl(positions, cost_basis, scenario_fv)
print(f"\nScenario: Settle at current FV ->  P&L = ${pnl_fv:>10.2f}")

# Scenario 2: All shorts settle at 0 (best case for shorts)
scenario_shorts_zero = {}
for team, pos in positions.items():
    if pos < 0:
        scenario_shorts_zero[team] = 0
    else:
        scenario_shorts_zero[team] = latest_fv.get(team, 0)
pnl_shorts_zero = calc_settlement_pnl(positions, cost_basis, scenario_shorts_zero)
print(f"Scenario: All shorts go to 0 ->    P&L = ${pnl_shorts_zero:>10.2f}")

# Scenario 3: Duke wins tournament
scenario_duke = {team: 0 for team in positions}  # everyone else R1
scenario_duke['Duke'] = 64
scenario_duke['Michigan'] = 32  # hypothetical runner-up
scenario_duke['Florida'] = 16
scenario_duke['Arizona'] = 16
pnl_duke = calc_settlement_pnl(positions, cost_basis, scenario_duke)
print(f"Scenario: Duke wins tournament ->   P&L = ${pnl_duke:>10.2f}")

# Scenario 4: Worst case - a heavily shorted team wins
# Find our largest short position
largest_short = min(positions.items(), key=lambda x: x[1])
scenario_worst = {team: 0 for team in positions}
scenario_worst[largest_short[0]] = 64  # our biggest short wins it all
pnl_worst = calc_settlement_pnl(positions, cost_basis, scenario_worst)
print(f"Scenario: {largest_short[0]} (biggest short) wins -> P&L = ${pnl_worst:>10.2f}")

# ============================================================
# 13. SUMMARY & RECOMMENDATIONS
# ============================================================

print("\n" + "=" * 80)
print("SUMMARY & KEY FINDINGS")
print("=" * 80)

net_pos = sum(positions.values())
print(f"""
PORTFOLIO OVERVIEW:
  Total trades executed: {len(trades)}
  Total volume: {sum(volume_by_team.values())} contracts
  Unique teams traded: {len(traded_teams)}
  Net cash flow: ${total_cash:.2f}
  MTM P&L (at current FV): ${total_mtm:.2f}
  Net position (all teams): {net_pos} contracts
  Theoretical edge captured: ${total_theoretical_edge:.2f}

KEY RISKS:
  1. Large short positions in low-FV teams (capped at 0, so profit is limited)
  2. Long Duke/Michigan/Arizona/Florida - heavily concentrated in favorites
  3. Bad trades with FV=0 detected (lines 163-165 in trades.csv)
  4. Worst-case scenario (biggest short wins): ${pnl_worst:.2f}

SETTLEMENT STRUCTURE CONCERN:
  Your example shows Michigan State winning and settling at 6, not 64.
  The bot uses 0/2/4/8/16/32/64 settlement structure.
  If the actual game uses DIFFERENT settlements, this is a CRITICAL issue.
  Please verify the settlement rules with the exchange.
""")
