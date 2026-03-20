import pandas as pd
from model import compute_fair_values, TEAM_TO_SYMBOL
from collections import defaultdict
import datetime

# Load real past trades
try:
    old_trades = pd.read_csv('trades.csv')
    print(f"--- OLD STRATEGY (Actual Trades) ---")
    print(f"Total Trades Executed: {len(old_trades)}")
    
    # Simple Mark to Market: Assuming final fair values or prices are roughly what they bought/sold at
    # Better yet, let's use the total 'edge' they thought they had
    edge_captured = (old_trades['qty'] * old_trades['edge']).sum()
    print(f"Total Theoretical Edge Captured (Old Model): ${edge_captured:.2f}")
except FileNotFoundError:
    print("No trades.csv found.")

print("\n" + "="*50 + "\n")

# Load market data
md = pd.read_csv('market_data.csv')
print(f"Loaded {len(md)} market data ticks.")

# Load live games
games = pd.read_csv('game_scores.csv')

# --- LIVE GAME INTEGRATION LOGIC ---
# The market data timestamps are around '2026-03-19'.
# The actual real-world game scores we scraped are from '2024-03-21'.
# To properly backtest NEW LIVE probabilities, we match the logic:
# 1. We re-map the 2024 game elapsed time to the 2026 market data simulation clock.
# 2. At each market tick, we check what the `score_diff` and `time_remaining` was 
#    for the matching team in `game_scores.csv`.
# 3. We pass those live stats to `model.compute_fair_values(live_games={...})`.
print("To map 'game_scores' into 'market_data' we align their start times:")
md['timestamp_dt'] = pd.to_datetime(md['timestamp'])
games['wallclock_dt'] = pd.to_datetime(games['wallclock_utc'])

print(f"Market Data Starts: {md['timestamp_dt'].min()}")
print(f"Game Scores Starts: {games['wallclock_dt'].min()}")

print("""
--- PROPOSED NEW ARCHITECTURE (LIVE BACKTEST) ---
For a fully accurate backtest:
1. Initialize Cash = $0, Positions = {}
2. Iterate through market_data.csv ticks.
3. For each tick timestamp, look up the closest elapsed match in game_scores.csv.
4. If a team is actively playing, compute their new live win probability using:
   `live_win_probability(score_diff, time_remaining)`
5. Run 50,000 simulations using the LIVE probabilities.
6. Compare the New Fair Value to `best_ask` or `best_bid`.
7. Execute fake trade and track Mark-to-Market!

(Note: Simulating 50k times *per* tick will be too slow, so we cache 
the fair values and only re-run `compute_fair_values()` when a game's score changes!)
""")
