import csv
from datetime import datetime, timedelta
from collections import defaultdict
import statistics

CSV_PATH = "C:/Users/Grigor/Desktop/NCAA/trades.csv"

# Parse trades
trades = []
with open(CSV_PATH, "r") as f:
    reader = csv.DictReader(f)
    for row in reader:
        trades.append({
            "timestamp": datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S"),
            "symbol": row["symbol"],
            "team": row["team"],
            "side": row["side"],
            "qty": int(row["qty"]),
            "price": float(row["price"]),
            "fair_value": float(row["fair_value"]),
            "edge": float(row["edge"]),
        })

print(f"Total trade rows: {len(trades)}")
total_qty = sum(t["qty"] for t in trades)
print(f"Total contracts traded: {total_qty}")
print(f"Time span: {trades[0]['timestamp']} to {trades[-1]['timestamp']}")
print()

# ============================================================
# 1. EDGE ANALYSIS
# ============================================================
print("=" * 70)
print("1. EDGE ANALYSIS")
print("=" * 70)

buy_edges = [t["edge"] for t in trades if t["side"] == "BUY"]
sell_edges = [t["edge"] for t in trades if t["side"] == "SELL"]
all_edges = [t["edge"] for t in trades]

# Weight by qty
buy_edges_w = []
sell_edges_w = []
all_edges_w = []
for t in trades:
    for _ in range(t["qty"]):
        all_edges_w.append(t["edge"])
        if t["side"] == "BUY":
            buy_edges_w.append(t["edge"])
        else:
            sell_edges_w.append(t["edge"])

print(f"Buy trades (rows): {len(buy_edges)}, Sell trades (rows): {len(sell_edges)}")
print(f"Buy contracts: {len(buy_edges_w)}, Sell contracts: {len(sell_edges_w)}")
print()
print(f"Avg edge (per row)   - BUY: {statistics.mean(buy_edges):.3f}  SELL: {statistics.mean(sell_edges):.3f}  ALL: {statistics.mean(all_edges):.3f}")
print(f"Avg edge (qty-weighted) - BUY: {statistics.mean(buy_edges_w):.3f}  SELL: {statistics.mean(sell_edges_w):.3f}  ALL: {statistics.mean(all_edges_w):.3f}")
print(f"Median edge (qty-weighted) - BUY: {statistics.median(buy_edges_w):.3f}  SELL: {statistics.median(sell_edges_w):.3f}  ALL: {statistics.median(all_edges_w):.3f}")
print()

# Negative edge trades
neg_edge = [t for t in trades if t["edge"] < 0]
print(f"NEGATIVE EDGE trades: {len(neg_edge)} rows, {sum(t['qty'] for t in neg_edge)} contracts")
for t in neg_edge:
    print(f"  {t['timestamp']}  {t['team']:20s}  {t['side']:4s}  qty={t['qty']}  price={t['price']:.2f}  fv={t['fair_value']:.2f}  edge={t['edge']:.2f}")
print()

# Low edge trades (< 2.0)
low_edge = [t for t in trades if 0 < t["edge"] < 2.0]
print(f"Low edge (0 < edge < 2.0): {len(low_edge)} rows, {sum(t['qty'] for t in low_edge)} contracts")
min_edge_trades = sorted(trades, key=lambda t: t["edge"])[:10]
print("10 lowest-edge trades:")
for t in min_edge_trades:
    print(f"  edge={t['edge']:+.3f}  {t['team']:20s}  {t['side']:4s}  qty={t['qty']}  price={t['price']:.2f}  fv={t['fair_value']:.2f}")
print()

# Edge distribution buckets
buckets = [("<0", 0), ("0-1.5", 0), ("1.5-2", 0), ("2-3", 0), ("3-5", 0), ("5-10", 0), ("10-20", 0), ("20+", 0)]
for t in trades:
    e = t["edge"]
    q = t["qty"]
    if e < 0: buckets[0] = (buckets[0][0], buckets[0][1] + q)
    elif e < 1.5: buckets[1] = (buckets[1][0], buckets[1][1] + q)
    elif e < 2: buckets[2] = (buckets[2][0], buckets[2][1] + q)
    elif e < 3: buckets[3] = (buckets[3][0], buckets[3][1] + q)
    elif e < 5: buckets[4] = (buckets[4][0], buckets[4][1] + q)
    elif e < 10: buckets[5] = (buckets[5][0], buckets[5][1] + q)
    elif e < 20: buckets[6] = (buckets[6][0], buckets[6][1] + q)
    else: buckets[7] = (buckets[7][0], buckets[7][1] + q)

print("Edge distribution (by contracts):")
for label, count in buckets:
    bar = "#" * (count // 5)
    print(f"  {label:>8s}: {count:5d}  {bar}")
print()

# ============================================================
# 2. ROUND-TRIP ANALYSIS (FIFO)
# ============================================================
print("=" * 70)
print("2. ROUND-TRIP ANALYSIS (FIFO)")
print("=" * 70)

# For each team, build FIFO queue of buys and sells
# A round trip = a buy matched with a sell (or vice versa)
team_buys = defaultdict(list)  # team -> [(price, qty_remaining, timestamp, fv)]
team_sells = defaultdict(list)
round_trips = []  # (team, buy_price, sell_price, qty, buy_ts, sell_ts)

for t in trades:
    team = t["team"]
    if t["side"] == "BUY":
        remaining = t["qty"]
        # Try to match against outstanding sells
        while remaining > 0 and team_sells[team]:
            sell_price, sell_remaining, sell_ts, sell_fv = team_sells[team][0]
            match_qty = min(remaining, sell_remaining)
            round_trips.append((team, t["price"], sell_price, match_qty, t["timestamp"], sell_ts))
            remaining -= match_qty
            sell_remaining -= match_qty
            if sell_remaining == 0:
                team_sells[team].pop(0)
            else:
                team_sells[team][0] = (sell_price, sell_remaining, sell_ts, sell_fv)
        if remaining > 0:
            team_buys[team].append([t["price"], remaining, t["timestamp"], t["fair_value"]])
    else:  # SELL
        remaining = t["qty"]
        # Try to match against outstanding buys
        while remaining > 0 and team_buys[team]:
            buy_price, buy_remaining, buy_ts, buy_fv = team_buys[team][0]
            match_qty = min(remaining, buy_remaining)
            round_trips.append((team, buy_price, t["price"], match_qty, buy_ts, t["timestamp"]))
            remaining -= match_qty
            buy_remaining -= match_qty
            if buy_remaining == 0:
                team_buys[team].pop(0)
            else:
                team_buys[team][0] = (buy_price, buy_remaining, buy_ts, buy_fv)
        if remaining > 0:
            team_sells[team].append([t["price"], remaining, t["timestamp"], t["fair_value"]])

# Round trip profit = sell_price - buy_price (per contract)
profitable = [(team, bp, sp, q, bts, sts) for team, bp, sp, q, bts, sts in round_trips if sp > bp]
unprofitable = [(team, bp, sp, q, bts, sts) for team, bp, sp, q, bts, sts in round_trips if sp <= bp]
total_rt_qty = sum(q for _, _, _, q, _, _ in round_trips)
total_rt_profit = sum((sp - bp) * q for _, bp, sp, q, _, _ in round_trips)

print(f"Total round trips: {len(round_trips)} ({total_rt_qty} contracts)")
print(f"Profitable: {len(profitable)} ({sum(q for _, _, _, q, _, _ in profitable)} contracts)")
print(f"Unprofitable: {len(unprofitable)} ({sum(q for _, _, _, q, _, _ in unprofitable)} contracts)")
print(f"Total realized PnL from round trips: ${total_rt_profit:.2f}")
print(f"Avg profit per round trip: ${total_rt_profit / len(round_trips):.2f}" if round_trips else "")
print(f"Avg profit per contract (round trips): ${total_rt_profit / total_rt_qty:.2f}" if total_rt_qty else "")
print()

# Per-team round trip summary
team_rt = defaultdict(lambda: {"count": 0, "qty": 0, "pnl": 0.0})
for team, bp, sp, q, bts, sts in round_trips:
    team_rt[team]["count"] += 1
    team_rt[team]["qty"] += q
    team_rt[team]["pnl"] += (sp - bp) * q

print(f"{'Team':25s} {'RTs':>5s} {'Qty':>5s} {'PnL':>10s} {'PnL/ct':>8s}")
print("-" * 60)
for team in sorted(team_rt, key=lambda t: team_rt[t]["pnl"]):
    r = team_rt[team]
    pnl_per = r["pnl"] / r["qty"] if r["qty"] else 0
    print(f"{team:25s} {r['count']:5d} {r['qty']:5d} ${r['pnl']:9.2f} ${pnl_per:7.2f}")
print()

# ============================================================
# 3. ADVERSE SELECTION / BAD TIMING
# ============================================================
print("=" * 70)
print("3. ADVERSE SELECTION / BAD TIMING")
print("=" * 70)

# For each trade, look at next trade in same team and see if price moved against us
team_trades = defaultdict(list)
for t in trades:
    team_trades[t["team"]].append(t)

adverse_buys = []  # bought, then price dropped
adverse_sells = []  # sold, then price rose

for team, tlist in team_trades.items():
    for i, t in enumerate(tlist):
        # Look at next few trades in same team
        future_prices = [tlist[j]["price"] for j in range(i + 1, min(i + 6, len(tlist)))]
        if not future_prices:
            continue
        min_future = min(future_prices)
        max_future = max(future_prices)
        if t["side"] == "BUY":
            drop = t["price"] - min_future
            if drop > 2.0:
                adverse_buys.append((t, drop, min_future))
        else:
            rise = max_future - t["price"]
            if rise > 2.0:
                adverse_sells.append((t, rise, max_future))

adverse_buys.sort(key=lambda x: -x[1])
adverse_sells.sort(key=lambda x: -x[1])

print(f"Adverse BUY trades (bought, then price dropped >$2 in next 5 same-team trades): {len(adverse_buys)}")
for t, drop, low in adverse_buys[:15]:
    print(f"  {t['timestamp']}  {t['team']:20s}  bought@{t['price']:.2f} fv={t['fair_value']:.2f}  -> dropped to {low:.2f} (drop={drop:.2f})")
print()

print(f"Adverse SELL trades (sold, then price rose >$2 in next 5 same-team trades): {len(adverse_sells)}")
for t, rise, high in adverse_sells[:15]:
    print(f"  {t['timestamp']}  {t['team']:20s}  sold@{t['price']:.2f} fv={t['fair_value']:.2f}  -> rose to {high:.2f} (rise={rise:.2f})")
print()

# ============================================================
# 4. POSITION CONCENTRATION RISK
# ============================================================
print("=" * 70)
print("4. POSITION CONCENTRATION RISK")
print("=" * 70)

positions = defaultdict(int)  # team -> net position (+ = long, - = short)
buy_cost = defaultdict(float)
sell_proceeds = defaultdict(float)
max_positions = defaultdict(int)
min_positions = defaultdict(int)

for t in trades:
    team = t["team"]
    if t["side"] == "BUY":
        positions[team] += t["qty"]
        buy_cost[team] += t["price"] * t["qty"]
    else:
        positions[team] -= t["qty"]
        sell_proceeds[team] += t["price"] * t["qty"]
    if positions[team] > max_positions[team]:
        max_positions[team] = positions[team]
    if positions[team] < min_positions[team]:
        min_positions[team] = positions[team]

print(f"{'Team':25s} {'Net Pos':>8s} {'Max Long':>9s} {'Max Short':>10s} {'BuyCost':>10s} {'SellProc':>10s}")
print("-" * 80)
for team in sorted(positions, key=lambda t: abs(positions[t]), reverse=True):
    if positions[team] != 0:
        print(f"{team:25s} {positions[team]:>8d} {max_positions[team]:>9d} {min_positions[team]:>10d} ${buy_cost[team]:>9.2f} ${sell_proceeds[team]:>9.2f}")
print()

total_long = sum(v for v in positions.values() if v > 0)
total_short = sum(v for v in positions.values() if v < 0)
print(f"Total long exposure: {total_long} contracts")
print(f"Total short exposure: {total_short} contracts")
print(f"Net position across all teams: {total_long + total_short}")
print()

# Gross dollar exposure estimate
gross_long_cost = sum(buy_cost[t] - sell_proceeds[t] for t in positions if positions[t] > 0)
print(f"Approximate long cost basis: ${gross_long_cost:.2f}")
print()

# ============================================================
# 5. MICHIGAN STATE DEEP DIVE
# ============================================================
print("=" * 70)
print("5. MICHIGAN STATE DEEP DIVE")
print("=" * 70)

msu_trades = [t for t in trades if "Michigan St" in t["team"] or "Michigan State" in t["team"]]
if not msu_trades:
    # Try broader search
    msu_trades = [t for t in trades if "Michigan" in t["team"] and "Michigan" != t["team"]]

if not msu_trades:
    # Check all team names
    print("Could not find Michigan State trades. Teams with 'Michigan':")
    for t in set(t["team"] for t in trades):
        if "ichigan" in t.lower():
            print(f"  '{t}'")
    # Try symbol
    msu_trades = [t for t in trades if "Mich" in t["symbol"] and t["symbol"] != "Michigan"]

if not msu_trades:
    print("Trying all teams with 'St' in symbol...")
    msu_syms = set()
    for t in trades:
        if "Mich" in t["symbol"]:
            msu_syms.add((t["symbol"], t["team"]))
    print(f"  Found: {msu_syms}")
    # Fallback: just look at the most-shorted team
    print("\nMost-shorted teams:")
    for team in sorted(positions, key=lambda t: positions[t])[:5]:
        print(f"  {team}: {positions[team]}")
    # Use the most shorted
    most_shorted = sorted(positions, key=lambda t: positions[t])[0]
    print(f"\nUsing most-shorted team: {most_shorted}")
    msu_trades = [t for t in trades if t["team"] == most_shorted]

print(f"\nFound {len(msu_trades)} trades, {sum(t['qty'] for t in msu_trades)} contracts")
msu_pos = 0
print(f"\n{'Timestamp':20s} {'Side':5s} {'Qty':>4s} {'Price':>7s} {'FV':>7s} {'Edge':>6s} {'NetPos':>7s}")
print("-" * 65)
for t in msu_trades:
    if t["side"] == "BUY":
        msu_pos += t["qty"]
    else:
        msu_pos -= t["qty"]
    print(f"{str(t['timestamp']):20s} {t['side']:5s} {t['qty']:4d} {t['price']:7.2f} {t['fair_value']:7.2f} {t['edge']:+6.2f} {msu_pos:>7d}")

print(f"\nFinal position: {msu_pos}")
msu_buys = sum(t["qty"] for t in msu_trades if t["side"] == "BUY")
msu_sells = sum(t["qty"] for t in msu_trades if t["side"] == "SELL")
msu_avg_sell = sum(t["price"] * t["qty"] for t in msu_trades if t["side"] == "SELL") / msu_sells if msu_sells else 0
msu_avg_buy = sum(t["price"] * t["qty"] for t in msu_trades if t["side"] == "BUY") / msu_buys if msu_buys else 0
msu_avg_fv_sell = sum(t["fair_value"] * t["qty"] for t in msu_trades if t["side"] == "SELL") / msu_sells if msu_sells else 0
print(f"Total bought: {msu_buys}, Total sold: {msu_sells}")
print(f"Avg buy price: ${msu_avg_buy:.2f}, Avg sell price: ${msu_avg_sell:.2f}")
print(f"Avg FV at sell time: ${msu_avg_fv_sell:.2f}")
print(f"If team wins ($100 payout), P&L on {msu_pos} short = ${-msu_pos * 100 + sum(t['price']*t['qty'] for t in msu_trades if t['side']=='SELL') - sum(t['price']*t['qty'] for t in msu_trades if t['side']=='BUY'):.2f}")
print(f"If team loses ($0 payout), P&L = ${sum(t['price']*t['qty'] for t in msu_trades if t['side']=='SELL') - sum(t['price']*t['qty'] for t in msu_trades if t['side']=='BUY'):.2f}")
print()

# ============================================================
# 6. FREQUENCY ANALYSIS
# ============================================================
print("=" * 70)
print("6. FREQUENCY ANALYSIS")
print("=" * 70)

# Time between trades
deltas = []
for i in range(1, len(trades)):
    d = (trades[i]["timestamp"] - trades[i - 1]["timestamp"]).total_seconds()
    deltas.append(d)

print(f"Total duration: {trades[-1]['timestamp'] - trades[0]['timestamp']}")
print(f"Avg time between trade rows: {statistics.mean(deltas):.1f}s")
print(f"Median time between trade rows: {statistics.median(deltas):.1f}s")
print(f"Min gap: {min(deltas):.1f}s, Max gap: {max(deltas):.1f}s")
print()

# Trades per hour
hour_counts = defaultdict(int)
for t in trades:
    key = t["timestamp"].strftime("%Y-%m-%d %H:00")
    hour_counts[key] += t["qty"]

sorted_hours = sorted(hour_counts.items())
print("Contracts traded per hour (top 20 busiest):")
for h, c in sorted(sorted_hours, key=lambda x: -x[1])[:20]:
    bar = "#" * (c // 5)
    print(f"  {h}: {c:4d}  {bar}")
print()

# Gaps > 10 minutes
big_gaps = [(i, deltas[i]) for i in range(len(deltas)) if deltas[i] > 600]
print(f"Idle periods (gap > 10 min): {len(big_gaps)}")
for i, gap in sorted(big_gaps, key=lambda x: -x[1])[:10]:
    print(f"  {trades[i]['timestamp']} -> {trades[i+1]['timestamp']}  gap={gap/60:.1f} min")
print()

# Bursts: multiple trades within 1 second
burst_groups = []
current_burst = [trades[0]]
for i in range(1, len(trades)):
    if (trades[i]["timestamp"] - current_burst[-1]["timestamp"]).total_seconds() <= 1:
        current_burst.append(trades[i])
    else:
        if len(current_burst) >= 3:
            burst_groups.append(current_burst[:])
        current_burst = [trades[i]]
if len(current_burst) >= 3:
    burst_groups.append(current_burst[:])

print(f"Burst events (3+ trade rows within 1 second): {len(burst_groups)}")
for bg in sorted(burst_groups, key=lambda x: -len(x))[:10]:
    teams = set(t["team"] for t in bg)
    total_q = sum(t["qty"] for t in bg)
    print(f"  {bg[0]['timestamp']}: {len(bg)} rows, {total_q} contracts, teams: {', '.join(teams)}")
print()

# ============================================================
# 7. BUY vs SELL IMBALANCE
# ============================================================
print("=" * 70)
print("7. BUY vs SELL IMBALANCE")
print("=" * 70)

total_buy_qty = sum(t["qty"] for t in trades if t["side"] == "BUY")
total_sell_qty = sum(t["qty"] for t in trades if t["side"] == "SELL")
total_buy_notional = sum(t["price"] * t["qty"] for t in trades if t["side"] == "BUY")
total_sell_notional = sum(t["price"] * t["qty"] for t in trades if t["side"] == "SELL")

print(f"Total BUY:  {total_buy_qty:5d} contracts, ${total_buy_notional:10.2f} notional")
print(f"Total SELL: {total_sell_qty:5d} contracts, ${total_sell_notional:10.2f} notional")
print(f"Ratio (sell/buy): {total_sell_qty/total_buy_qty:.2f}x by quantity, {total_sell_notional/total_buy_notional:.2f}x by notional")
print()

# Per-team imbalance
print(f"{'Team':25s} {'Buys':>6s} {'Sells':>6s} {'Net':>6s} {'Bias':>8s}")
print("-" * 55)
team_buyside = defaultdict(int)
team_sellside = defaultdict(int)
for t in trades:
    if t["side"] == "BUY":
        team_buyside[t["team"]] += t["qty"]
    else:
        team_sellside[t["team"]] += t["qty"]

all_teams_traded = set(team_buyside.keys()) | set(team_sellside.keys())
for team in sorted(all_teams_traded, key=lambda t: abs(team_buyside[t] - team_sellside[t]), reverse=True):
    b = team_buyside[team]
    s = team_sellside[team]
    bias = "BUY" if b > s else "SELL" if s > b else "FLAT"
    print(f"{team:25s} {b:6d} {s:6d} {b-s:>+6d} {bias:>8s}")
print()

# ============================================================
# 8. SLIPPAGE / FILL QUALITY
# ============================================================
print("=" * 70)
print("8. SLIPPAGE / FILL QUALITY")
print("=" * 70)

# For buys: slippage = price - fair_value (positive = bad, paying more than FV)
# For sells: slippage = fair_value - price (positive = bad, selling below FV)
# Actually edge = |price - fair_value| so:
# For buys: we buy at price < fair_value, so edge = fair_value - price > 0 means good
# For sells: we sell at price > fair_value, so edge = price - fair_value > 0 means good

buy_slippage = []  # fair_value - price for buys (positive = good)
sell_slippage = []  # price - fair_value for sells (positive = good)

for t in trades:
    if t["side"] == "BUY":
        slip = t["fair_value"] - t["price"]
        for _ in range(t["qty"]):
            buy_slippage.append(slip)
    else:
        slip = t["price"] - t["fair_value"]
        for _ in range(t["qty"]):
            sell_slippage.append(slip)

print("Edge = how much better than fair value we traded at (positive = good)")
print(f"BUY fills:  avg edge = {statistics.mean(buy_slippage):+.3f}, median = {statistics.median(buy_slippage):+.3f}")
print(f"SELL fills: avg edge = {statistics.mean(sell_slippage):+.3f}, median = {statistics.median(sell_slippage):+.3f}")
print()

# Bad fills (negative edge)
bad_buys = [t for t in trades if t["side"] == "BUY" and t["fair_value"] - t["price"] < 0]
bad_sells = [t for t in trades if t["side"] == "SELL" and t["price"] - t["fair_value"] < 0]
print(f"Bad BUY fills (price > fair_value): {len(bad_buys)} rows, {sum(t['qty'] for t in bad_buys)} contracts")
for t in bad_buys:
    print(f"  {t['timestamp']}  {t['team']:20s}  price={t['price']:.2f}  fv={t['fair_value']:.2f}  overpaid={t['price']-t['fair_value']:.2f}")
print()
print(f"Bad SELL fills (price < fair_value): {len(bad_sells)} rows, {sum(t['qty'] for t in bad_sells)} contracts")
for t in bad_sells:
    print(f"  {t['timestamp']}  {t['team']:20s}  price={t['price']:.2f}  fv={t['fair_value']:.2f}  undersold={t['fair_value']-t['price']:.2f}")
print()

# ============================================================
# 9. TEAMS THAT LOST MONEY (from round trips)
# ============================================================
print("=" * 70)
print("9. TEAMS WITH NEGATIVE REALIZED PnL (ROUND TRIPS)")
print("=" * 70)

losing_teams = {t: r for t, r in team_rt.items() if r["pnl"] < 0}
print(f"Teams with negative realized PnL: {len(losing_teams)}")
print(f"{'Team':25s} {'RTs':>5s} {'Qty':>5s} {'PnL':>10s} {'PnL/ct':>8s}")
print("-" * 60)
for team in sorted(losing_teams, key=lambda t: losing_teams[t]["pnl"]):
    r = losing_teams[team]
    pnl_per = r["pnl"] / r["qty"] if r["qty"] else 0
    print(f"{team:25s} {r['count']:5d} {r['qty']:5d} ${r['pnl']:9.2f} ${pnl_per:7.2f}")

total_loss = sum(r["pnl"] for r in losing_teams.values())
print(f"\nTotal realized losses: ${total_loss:.2f}")

winning_teams = {t: r for t, r in team_rt.items() if r["pnl"] > 0}
total_win = sum(r["pnl"] for r in winning_teams.values())
print(f"Total realized gains: ${total_win:.2f}")
print(f"Net realized PnL: ${total_win + total_loss:.2f}")
print()

# Worst individual round trips
print("Worst 15 individual round trips:")
worst_rts = sorted(round_trips, key=lambda x: (x[2] - x[1]) * x[3])[:15]
for team, bp, sp, q, bts, sts in worst_rts:
    pnl = (sp - bp) * q
    print(f"  {team:25s}  buy@{bp:.2f} sell@{sp:.2f}  qty={q}  PnL=${pnl:.2f}  {bts} -> {sts}")
print()

# ============================================================
# 10. PRICE PATTERNS / CHURNING
# ============================================================
print("=" * 70)
print("10. PRICE PATTERNS / CHURNING DETECTION")
print("=" * 70)

# For each team, look for repeated trades at very similar prices
for team in sorted(team_trades.keys()):
    tlist = team_trades[team]
    if len(tlist) < 5:
        continue
    prices = [t["price"] for t in tlist]
    # Check if many trades at same price
    from collections import Counter
    price_counts = Counter()
    for t in tlist:
        rounded = round(t["price"], 1)
        price_counts[rounded] += t["qty"]

    # Find repeated price levels
    repeated = {p: c for p, c in price_counts.items() if c >= 5}
    if repeated:
        total_team_qty = sum(t["qty"] for t in tlist)
        repeated_qty = sum(repeated.values())
        if repeated_qty > total_team_qty * 0.5 and total_team_qty >= 10:
            print(f"\n{team}: {total_team_qty} contracts, {len(tlist)} trades")
            print(f"  {repeated_qty}/{total_team_qty} contracts ({100*repeated_qty/total_team_qty:.0f}%) at repeated price levels:")
            for p in sorted(repeated, key=lambda x: -repeated[x])[:5]:
                print(f"    ${p:.1f}: {repeated[p]} contracts")
            # Check buy/sell at same level
            buy_prices = Counter()
            sell_prices = Counter()
            for t in tlist:
                rp = round(t["price"], 1)
                if t["side"] == "BUY":
                    buy_prices[rp] += t["qty"]
                else:
                    sell_prices[rp] += t["qty"]
            overlap = set(buy_prices.keys()) & set(sell_prices.keys())
            if overlap:
                print(f"  CHURNING: buying AND selling at same price levels:")
                for p in sorted(overlap):
                    if buy_prices[p] >= 2 and sell_prices[p] >= 2:
                        print(f"    ${p:.1f}: bought {buy_prices[p]}, sold {sell_prices[p]}")

print()

# ============================================================
# SUMMARY
# ============================================================
print("=" * 70)
print("SUMMARY OF KEY FINDINGS")
print("=" * 70)
print(f"1. {len(neg_edge)} negative-edge trade rows ({sum(t['qty'] for t in neg_edge)} contracts) - bot traded AGAINST itself")
print(f"2. Sell-heavy bias: {total_sell_qty} sells vs {total_buy_qty} buys ({total_sell_qty/total_buy_qty:.1f}x)")
print(f"3. Open position risk: {sum(abs(v) for v in positions.values() if v != 0)} contracts in open positions")
biggest_short = min(positions.items(), key=lambda x: x[1])
biggest_long = max(positions.items(), key=lambda x: x[1])
print(f"4. Biggest short: {biggest_short[0]} at {biggest_short[1]} contracts")
print(f"5. Biggest long: {biggest_long[0]} at {biggest_long[1]} contracts")
print(f"6. Net realized PnL: ${total_rt_profit:.2f}")
print(f"7. {len(adverse_buys)} adverse buy events, {len(adverse_sells)} adverse sell events")
print(f"8. Bad fills (wrong side of FV): {len(bad_buys)} buys + {len(bad_sells)} sells")
