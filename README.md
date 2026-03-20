# NCAA Market Madness Trading Bot

Algorithmic trading bot for DRW's Market Madness simulator (games.drw.com). Trades 68 NCAA tournament team contracts that settle based on how far each team advances (Champion=64, Runner-up=32, F4=16, E8=8, S16=4, R32=2, R1 exit=0).

---

## How It Works (Full Pipeline)

### Step 1: Team Ratings + Live Odds
- **Baseline**: KenPom AdjEM ratings for all 68 teams (fallback when APIs are unavailable)
- **Live odds integration**: Fetches real-time bookmaker consensus from **The Odds API** (15+ US sportsbooks) and **Kalshi** prediction market prices
- Bookmaker moneylines are devigged (vig removed) and averaged across all available books
- For scheduled/live matchups with bookmaker data, uses devigged market probability directly instead of rating-based model
- For future hypothetical matchups (later bracket rounds), uses **market-calibrated ratings** — original AdjEM values nudged toward market consensus with 60% damping
- Win probability formula (when no market data): `P(A beats B) = 1 / (1 + 10^(-AdjEM_diff / 11))`

### Step 2: Monte Carlo Fair Values + Market Blending
- Simulates the **entire 68-team bracket 100,000 times**
- Each simulation uses market-calibrated ratings and bookmaker matchup overrides when available
- Fair value = average settlement across all simulations (weighted sum of 0/2/4/8/16/32/64)
- MC fair values are then **blended with championship market probabilities** (35% market weight, 65% MC) from bookmakers and Kalshi
- Total FV across all teams always sums to exactly **224** (preserved after blending)
- Recomputes every **15 seconds during live games**, 30 seconds otherwise

### Step 3: Live Game Adjustments
When a game is in progress, the model adjusts win probability using live data from ESPN:

```
expected_margin = current_score_diff + (AdjEM_diff * time_remaining / 2400)
std_dev = sigma_adj * sqrt(time_remaining / 2400)    # non-linear time scaling
P(win) = normal_CDF(expected_margin / std_dev)
```

Key features:
- **Pace-adjusted variance**: Fast-paced games (e.g., Gonzaga at 72 possessions) get higher sigma than slow games (e.g., Virginia at 62). Sigma scales by `sqrt(pace / 68)`
- **Non-linear time decay**: Variance uses `sqrt(time_factor)` so uncertainty doesn't collapse too quickly in final minutes
- **Overtime handling**: When tied at buzzer, uses `0.5 + 0.69 * (pregame_prob - 0.5)` — the stronger team still has an edge in OT, not a coin flip
- **Timeout extension**: Trailing team's timeouts add ~15 seconds of effective game time each (more possessions)

### Step 4: Trading Decisions
For each team contract, the bot evaluates whether to buy, sell, or hold:

**Edge-based trading**: Only trades when `|market_price - fair_value| > minimum_edge` (1.5 points)

- **BUY** when `skewed_FV - best_ask > required_edge` (market underprices the team)
- **SELL/SHORT** when `best_bid - skewed_FV > required_edge` (market overprices the team)

**Avellaneda-Stoikov inventory skewing**: The fair value is adjusted based on current position:
```
skewed_FV = fair_value - (position * gamma)    # gamma = 0.05
```
If we're long 60 contracts, the skewed FV drops by 3 points — making us more eager to sell and less eager to buy more. This naturally caps position sizes and reduces risk without hard limits.

**Option Value (OV)** — only active during live games:
```
OV = 0.39 * N^0.42 * (1 + 16.12 * p * (1-p))    # N = time blocks remaining
```
Opening new positions requires `MIN_EDGE + OV` (higher bar). Closing/reducing positions requires only `MIN_EDGE` (lower bar). This prevents selling a winner too cheaply mid-game while still allowing quick exits.

**Fractional Kelly sizing**: Position size scales with edge magnitude:
```
kelly_fraction = (edge / variance) * 64
qty = kelly_fraction * 0.15 * 500    # capped at 10 per order
```

### Step 5: Risk Controls

| Control | Value | Purpose |
|---------|-------|---------|
| Max position | 80 contracts/team | Buffer below exchange limit of 100 |
| Price boundaries | 6.4 - 57.6 | Only trade in 10%-90% probability range |
| Spread limit | 4.0 points | Don't trade illiquid markets (edge lost to spread) |
| Order cooldown | 5 seconds/symbol | Prevent rapid-fire on same contract |
| PnL floor | -$500,000 | Risk-reducing only mode if breached |
| Late-game firewall | Final 4 minutes | Only risk-reducing trades (no new positions) |
| Max order size | 10 contracts | Split large orders to reduce market impact |
| Wash trade detection | Spread <= 0.5, mid far from FV | Skip toxic fake liquidity |
| API failsafe | 3 consecutive ESPN failures | Pause ALL trading until data recovers |

### Step 6: Market Making
The bot also posts passive **limit orders** near fair value:
- Bid at `skewed_FV - 1.5` (willing to buy below value)
- Ask at `skewed_FV + 1.5` (willing to sell above value)
- Quantity: 3 contracts per side
- Uses inventory-skewed FV so orders naturally lean toward reducing position

### Step 7: Data Pipeline

**ESPN API** (live game state):
- **Live scores**: Fetched every **10 seconds** — score, clock, period for all in-progress games
- **Eliminations**: Checked every **60 seconds** — scans last 5 days of results for final scores
- **Failsafe**: If ESPN fails 3+ times in a row, ALL trading stops immediately. Resumes automatically when API recovers. The bot will never trade on stale data.

**The Odds API** (bookmaker consensus):
- Fetches devigged moneylines from 15+ US bookmakers (DraftKings, FanDuel, Caesars, etc.)
- Championship/outright winner futures for all tournament teams
- Polling: every **10 min** during live games, **15 min** pre-game, **1 hour** for futures
- Budget: 500 free credits/month (~2 credits per startup, ~15 per game day)

**Kalshi** (prediction market):
- Streams real-time tournament advancement prices via **public WebSocket** (free, unlimited, no auth)
- Game winner contracts, Final Four, Elite Eight, championship probabilities
- Falls back to REST polling every 2 min if WebSocket disconnects

---

## File Structure

| File | Purpose |
|------|---------|
| `bot.py` | Main trading bot — connects to DRW exchange, runs trading loop |
| `model.py` | Monte Carlo engine — team ratings, bracket sim, fair values, live win probability |
| `odds_api.py` | External odds integration — The Odds API client, Kalshi WebSocket, rating calibration, FV blending |
| `live_data.py` | ESPN API integration — live scores, eliminations, team name mapping |
| `trading_client.py` | DRW exchange client — WebSocket connection, order management |
| `backtester.py` | Simple backtester replaying market_data.csv |
| `backtest_compare.py` | Strategy comparison backtester (OLD vs NEW with Gemini improvements) |
| `analysis.py` | Day 1 portfolio analysis — P&L, positions, bad trades, risk scenarios |
| `deep_analysis.py` | Market microstructure analysis, multi-strategy backtesting, grid search |
| `gemini_report.md` | Research report sent to Gemini for strategy improvements |
| `trades.csv` | Live trade log (timestamp, symbol, side, qty, price, FV, edge) |
| `market_data.csv` | Market data snapshots (bid, ask, FV, spread per symbol) |
| `bot.log` | Runtime log (trades, errors, status, ESPN updates) |
| `restart_bot.ps1` | Kill old bot + restart updated version + disable sleep mode |

---

## How to Run

### Quick Restart (kills old bot, starts new one, prevents sleep)
```powershell
.\restart_bot.ps1
```

### Manual Background Start (survives terminal close)
```powershell
pythonw bot.py
```

### Foreground Start (see live logs)
```powershell
python bot.py
```

### Prevent Sleep Mode (if not using restart script)
```powershell
powercfg -change -standby-timeout-ac 0
powercfg -change -hibernate-timeout-ac 0
```

### Stop the Bot
```powershell
# PowerShell
Stop-Process -Name "pythonw", "python" -Force -ErrorAction SilentlyContinue

# Or use Task Manager > Details > pythonw.exe > End task
```

---

## Monitoring

- **`bot.log`** — Real-time: trades, ESPN updates, errors, API failsafe alerts
- **`trades.csv`** — Every execution with edge at time of trade
- **`market_data.csv`** — Market snapshots for post-session analysis
- **DRW Web UI** — `https://games.drw.com/games/trading-simulator/160`

---

## Known Issues Fixed (v5 — March 20, 2026)

**Limit orders using skewed FV caused position blowthrough** — Market-making limit orders used A-S inventory-skewed fair value for price placement. When long +61 Michigan State (FV=5.0), skewed FV dropped to 2.0, posting asks at 3.5 — instantly filled by other participants. This drove the position from +61 to -65 in minutes, then A-S skew forced expensive buybacks at 6.7–7.1 (above raw FV of 6.0), resulting in negative-edge trades. Fixed by: (1) limit orders now use raw FV for price placement, and (2) limit order qty is capped to prevent flipping position direction — if long, asks only reduce to flat; if short, bids only cover to flat.

## Known Issues Fixed (v4 — March 20, 2026)

**Missing ESPN name variants** — ESPN_TO_MODEL was missing `"California Baptist Lancers"` and `"Miami OH RedHawks"` alternate name formats. If ESPN returned either variant, the team would fail to resolve, causing eliminated teams to remain "alive" in the model or live scores to be silently dropped.

**Backtest crash (backtest_compare.py)** — The head-to-head comparison section referenced an undefined variable `new` (7 occurrences) instead of `new_best`, causing a NameError crash whenever the backtest reached the comparison output.

**Full stress test validation** — Ran 433 automated tests across all API integrations: Odds API, ESPN, exchange symbols, Kalshi title parsing, formula correctness, bracket integrity, and cross-API consistency. All substring collision pairs (Michigan/Michigan State, Iowa/Iowa State, Tennessee/Tennessee State, Miami FL/Miami OH, etc.) verified correct across every mapping system.

## Known Issues Fixed (v3 — March 20, 2026)

**Team name mapping: step 2 substring collision** — The v2 fix applied word-boundary regex to step 3 (TEAM_RATINGS fallback) but step 2 (partial match against mapping dicts) still used bare `in` substring checks bidirectionally. If an API sent a shortened name like `"Michigan"`, the length-descending sort caused it to match `"Michigan State Spartans"` first — returning **Michigan State** instead of Michigan. Same issue for Iowa/Iowa State, Tennessee/Tennessee State, Texas/Texas Tech. Fixed by removing the dangerous input-in-key direction and using word-boundary regex for key-in-input only.

**Pace never passed to live_win_probability()** — Bot calculated per-game pace and stored it in `live_games_map`, but MC simulation always used the default 68.0 possessions/game. High-tempo teams (Duke 76, Florida 75) and low-tempo teams (Virginia 62, Wisconsin 64) were modeled identically during live games. Fixed in both `simulate_round()` and `resolve_first_four()`.

## Known Issues Fixed (v2 — March 20, 2026)

**Team name mapping bugs** — Fuzzy/substring fallbacks in all API integrations (ESPN, Odds API, Kalshi, exchange symbol matching) could map "Michigan State" to "Michigan" (and similar pairs: Iowa/Iowa State, Texas/Texas Tech, Tennessee/Tennessee State). All 7 fallback locations now use word-boundary regex matching with longest-name-first ordering.

**Edge/Kelly sizing mismatch** — Trade entry used inventory-skewed FV for edge calculation but Kelly sizing used raw FV, causing position sizes inconsistent with the edge that triggered the trade. Now both use skewed FV.

**Kalshi REST price interpretation** — REST fields ending in `_dollars` are 0.00–1.00 range, not cents. Were being divided by 100x, making Kalshi data nearly invisible in fair value blending.

**Kalshi WS round overwrite** — A price update for one ticker (e.g., championship) was overwriting all advancement round probabilities for that team. Now only updates the matching round.

**Operator precedence** — `"champion" or "win" and "tournament"` parsed incorrectly due to `and` binding tighter than `or`. Added explicit parentheses.

**Spread limit constant** — Hardcoded `3.0` instead of using the `SPREAD_LIMIT = 4.0` config constant.

**Wash trade threshold** — Was `0.05` on a 0–64 price scale (impossible to trigger). Changed to `0.5`.

**Uninitialized attributes** — `live_games_map`, `data_stale`, `risk_reducing_only` relied on `getattr` fallbacks instead of proper `__init__` initialization.

---

## Results

### Day 1 (March 19 — Pre-Tournament)
- 459 trades, 955 contracts traded, +$2,673 MTM P&L
- Long: Duke (+30), Arizona (+36), Michigan (+41), Florida (+48)
- Short: 24 teams including Nebraska (-80), Connecticut (-80), UCLA (-71)
- Key insight: Market systematically overprices mid/low-tier teams vs our model — only 4 teams underpriced

### Day 1–2 Cumulative (March 19–20)
- 1,395 trades, 2,939 contracts traded
- Cash: $3,940.73 | P&L: $2,134.78
- Top winner: Michigan (+$916 realized, ~$1,000 unrealized) — bought heavily at $6.75–7.55 vs model FV of $21+
- Other winners: Iowa State (+$385), Nebraska (+$130), Purdue (+$100), Houston (+$85)
- Avg edge per contract: $2.39 | Trades per hour: 38
