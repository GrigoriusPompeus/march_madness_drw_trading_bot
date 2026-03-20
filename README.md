# NCAA Market Madness Trading Bot

Algorithmic trading bot for DRW's Market Madness simulator (games.drw.com). Trades 68 NCAA tournament team contracts that settle based on how far each team advances (Champion=64, Runner-up=32, F4=16, E8=8, S16=4, R32=2, R1 exit=0).

---

## How It Works (Full Pipeline)

### Step 1: Team Ratings
- Uses **KenPom Adjusted Efficiency Margin (AdjEM)** ratings for all 68 teams
- Top teams: Duke (35.2), Arizona (33.5), Michigan (32.8), Florida (30.4), Houston (28.7)
- Win probability between two teams: `P(A beats B) = 1 / (1 + 10^(-AdjEM_diff / 11))`
- A 5-point AdjEM gap gives ~73% win probability

### Step 2: Monte Carlo Fair Values
- Simulates the **entire 68-team bracket 100,000 times**
- Each simulation plays out every game using the logistic win probability
- Fair value = average settlement across all simulations (weighted sum of 0/2/4/8/16/32/64)
- Example: if Duke wins the championship in 40% of sims, reaches F4 in 25%, etc., its FV might be ~37
- Total FV across all teams always sums to exactly **224** (the total points distributed)
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
| Spread limit | 3.0 points | Don't trade illiquid markets (edge lost to spread) |
| Order cooldown | 5 seconds/symbol | Prevent rapid-fire on same contract |
| PnL floor | -$500,000 | Risk-reducing only mode if breached |
| Late-game firewall | Final 4 minutes | Only risk-reducing trades (no new positions) |
| Max order size | 10 contracts | Split large orders to reduce market impact |
| Wash trade detection | Spread <= 0.05, mid far from FV | Skip toxic fake liquidity |
| API failsafe | 3 consecutive ESPN failures | Pause ALL trading until data recovers |

### Step 6: Market Making
The bot also posts passive **limit orders** near fair value:
- Bid at `skewed_FV - 1.5` (willing to buy below value)
- Ask at `skewed_FV + 1.5` (willing to sell above value)
- Quantity: 3 contracts per side
- Uses inventory-skewed FV so orders naturally lean toward reducing position

### Step 7: Data Pipeline (ESPN API)
- **Live scores**: Fetched every **10 seconds** — score, clock, period for all in-progress games
- **Eliminations**: Checked every **60 seconds** — scans last 5 days of results for final scores
- **Failsafe**: If ESPN fails 3+ times in a row, ALL trading stops immediately. Resumes automatically when API recovers. The bot will never trade on stale data.

---

## File Structure

| File | Purpose |
|------|---------|
| `bot.py` | Main trading bot — connects to DRW exchange, runs trading loop |
| `model.py` | Monte Carlo engine — team ratings, bracket sim, fair values, live win probability |
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

## Day 1 Results (March 19, 2026 — Pre-Tournament)

- 459 trades, 955 contracts traded, +$2,673 MTM P&L
- Long: Duke (+30), Arizona (+36), Michigan (+41), Florida (+48)
- Short: 24 teams including Nebraska (-80), Connecticut (-80), UCLA (-71)
- Key insight: Market systematically overprices mid/low-tier teams vs our model — only 4 teams underpriced
