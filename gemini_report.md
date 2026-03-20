# NCAA March Madness Trading Bot - Comprehensive Report & Research Task for Gemini

## TASK FOR GEMINI

I'm running an algorithmic trading bot on DRW's March Madness trading simulator. Each of the 68 NCAA tournament teams is a tradeable contract that settles based on how far the team advances. I need you to research live sports betting market dynamics, in-game probability models, and optimal trading strategies so I can improve my bot's live-game performance. Details of my system, Day 1 results, and specific questions are below.

---

## 1. WHAT THE BOT DOES

### Platform
- **DRW's Market Madness** trading simulator (games.drw.com)
- 68 tradeable contracts, one per NCAA tournament team
- Contracts settle based on tournament advancement:
  - **Champion: 64 points**, Runner-up: 32, Final Four: 16, Elite Eight: 8, Sweet Sixteen: 4, Round of 32: 2, **First Round exit: 0**
- Continuous limit order book (bid/ask), can go long or short
- Position limit: 100 contracts per team (we use 80)
- PnL floor: -$500,000 triggers risk-reducing-only mode

### Our Model (Fair Value Computation)
- **Monte Carlo simulation** (100,000 iterations) of the full 68-team bracket
- Team strength based on **KenPom Adjusted Efficiency Margin (AdjEM)** ratings
- Win probability uses logistic model: `P(A beats B) = 1 / (1 + 10^(-diff/11))` where diff = AdjEM_A - AdjEM_B
- A 5-point AdjEM gap gives ~73% win probability
- Fair value = expected settlement value across all simulations (weighted average of 0/2/4/8/16/32/64 based on simulated paths)

### Live Game Adjustments
- During live games, win probability incorporates **score differential + time remaining**:
  ```
  expected_margin = current_score_diff + (rating_diff * time_factor)
  variance_sd = 11.0 * sqrt(time_factor)    # time_factor = seconds_remaining / 2400
  P(win) = normal_CDF(expected_margin / variance_sd)
  ```
- Model recomputes fair values every 30 seconds
- ESPN API provides live scores, eliminated teams, game clock

### Trading Logic
- **Edge-based**: only trades when `|market_price - fair_value| > minimum_edge` (currently 1.5 points)
- **Fractional Kelly criterion** for position sizing (fraction = 0.15)
- **BUY** when fair_value > best_ask + min_edge (market is underpricing the team)
- **SELL/SHORT** when best_bid > fair_value + min_edge (market is overpricing the team)
- Spread limit: won't trade if bid-ask spread > 3.0
- Price boundaries: only trades contracts priced between 6.4 and 57.6 (10%-90% of max)
- 5-second cooldown between orders on the same symbol
- **Option Value (OV)**: when holding a long position, requires extra edge to sell (accounts for time value)
- **Late-game firewall**: in final 4 minutes of a game, only allows risk-reducing trades
- **Dynamic edge scaling**: required edge increases from 5% to 15% of max (3.2 to 9.6 points) as game progresses
- Also posts passive limit orders near fair value for market-making

### Top 10 Team Ratings (AdjEM) Used by Our Model
| Team | AdjEM | Model Fair Value | Market Mid Price |
|------|-------|-----------------|-----------------|
| Duke | 35.2 | 37.07 | 25.52 |
| Arizona | 33.5 | 28.56 | 21.12 |
| Michigan | 32.8 | 28.78 | 25.26 |
| Florida | 30.4 | 18.37 | 16.71 |
| Houston | 28.7 | 11.34 | 15.04 |
| Iowa State | 27.9 | 11.62 | 12.72 |
| Illinois | 26.3 | 7.14 | 9.65 |
| Purdue | 25.6 | 7.78 | 10.17 |
| Michigan State | 24.1 | 6.77 | 8.71 |
| Gonzaga | 23.4 | 5.87 | 6.86 |

---

## 2. DAY 1 RESULTS (March 19, 2026 - Pre-Tournament / First Four)

### Portfolio Summary
- **459 trades** executed across 28 teams
- **955 total contracts** traded (155 bought, 800 sold)
- **Net cash flow: +$1,950** (heavily net seller)
- **Mark-to-market P&L: +$2,673** (at current fair values)
- **Theoretical edge captured: $2,351** (sum of edge * qty on all trades)
- **3 bad trades detected**: bot bought Michigan (3x@25.58) and Arizona (8x@21.8-21.9) when fair_value momentarily dropped to 0.0 (data glitch), costing ~$251

### Current Positions (End of Day 1)
| Team | Position | Direction | FV | Market Mid | Avg Trade Price |
|------|----------|-----------|-----|-----------|----------------|
| Nebraska | -80 | SHORT | 3.10 | 5.86 | ~5.46 |
| Connecticut | -80 | SHORT | 5.80 | 9.33 | ~9.25 |
| UCLA | -71 | SHORT | 1.74 | 3.13 | ~3.25 |
| Michigan State | -66 | SHORT | 6.72 | 8.71 | ~8.36 |
| Illinois | -65 | SHORT | 7.22 | 9.65 | ~9.57 |
| Iowa State | -52 | SHORT | 11.58 | 12.72 | ~15.0 |
| Santa Clara | -50 | SHORT | 0.48 | 2.02 | ~2.78 |
| Florida | +48 | LONG | 18.41 | 16.71 | ~16.72 |
| Michigan | +41 | LONG | 28.83 | 25.26 | ~25.17 |
| St. John's | -38 | SHORT | 2.32 | 4.20 | ~4.68 |
| Arizona | +36 | LONG | 28.48 | 21.12 | ~21.86 |
| Duke | +30 | LONG | 37.11 | 25.52 | ~26.0 |
| Houston | -30 | SHORT | 11.34 | 15.04 | ~14.88 |
| Arkansas | -30 | SHORT | 3.42 | 5.51 | ~5.53 |
| Wisconsin | -30 | SHORT | 2.53 | 4.55 | ~5.10 |
| Virginia | -28 | SHORT | 3.92 | 5.59 | ~5.90 |
| Purdue | -28 | SHORT | 7.76 | 10.17 | ~10.39 |
| (+ 11 more smaller positions) | | | | | |

### Key Trading Patterns
- **Only 4 teams bought** (Duke, Arizona, Michigan, Florida) - all top-4 seeds / 1-seeds
- **24 teams shorted** - the market systematically overprices mid/low-tier teams vs our model
- Largest single-trade edge: Duke BUY at 26.0 with FV=37.12 (edge=11.12)
- Most-traded team by count: UCLA (47 trades), Michigan State (41 trades), Nebraska (37 trades)

### Risk Profile
- **Total long exposure**: $4,204 (pos * FV)
- **Total short exposure**: $3,481 (|pos| * FV)
- **Total short max loss**: $51,200 (if all shorted teams win the championship at 64)
- **Worst single scenario**: Nebraska wins tournament -> -$3,170
- **Best scenario**: All shorts settle at 0 -> +$6,154

---

## 3. MARKET MICROSTRUCTURE FINDINGS

### 3A. Systematic Mispricing
The market **overprices nearly every team** relative to our Monte Carlo fair values:
- **100% of snapshots** show market mid > model FV for 30+ teams
- Only 4 teams are **underpriced** by the market: Duke (-11.49), Arizona (-7.43), Michigan (-3.51), Florida (-1.65)
- This is either: (a) our model undervalues non-favorites, (b) market participants have retail bias toward longshots, or (c) a combination

### 3B. Price Behavior
All 8 major teams show **strong mean-reversion** in price movements:
- Michigan: autocorrelation = -0.813 (very strong mean reversion)
- Florida: autocorrelation = -0.874
- Duke: autocorrelation = -0.453
- Implication: prices bounce around FV, don't trend. Don't chase momentum.

### 3C. Spread & Liquidity
- Wide-spread teams (market making opportunities): Connecticut (avg 1.17), Michigan State (1.09), Nebraska (1.02)
- Tight-spread teams: Purdue (0.01), McNeese (0.01), Troy (0.01) - but these are likely wash trading

### 3D. Wash Trading Signals
Massive artificial tight spread activity detected:
- **Purdue**: 681 instances of spread=0.01 with bid far from FV (bid=10.39, FV=7.76)
- **Arkansas**: 649 instances
- **Houston**: 639 instances
- **Nebraska, Connecticut**: 638-639 instances each
- Pattern: someone posts matching bid/ask 1 cent apart, far above FV. Could be wash trading to create fake volume, or could be a market maker with a different model.

### 3E. Exploit Opportunities Identified
1. **Persistent overpricing in mid-tier teams**: Houston (+3.70 over FV), Connecticut (+3.51), Nebraska (+2.76), Illinois (+2.45), Purdue (+2.39)
2. **Persistent underpricing in top-4**: Duke (-11.49), Arizona (-7.43) - massive and persistent
3. **Virginia had a locked market** at one point (bid >= ask) - free arbitrage if caught in real-time

---

## 4. BACKTESTING RESULTS (14 Strategies Tested)

| Strategy | MTM P&L | Max Loss | Sharpe* | Trades |
|----------|---------|----------|---------|--------|
| WHALE (edge=0.5, kelly=0.3) | $9,822 | -$284K | 0.345 | 654 |
| AGGRESSIVE (edge=1.0, pos=100) | $8,878 | -$244K | 0.363 | 442 |
| **OUR CURRENT (edge=1.5, pos=80)** | **$5,346** | **-$97K** | **0.552** | **193** |
| SELL ONLY | $3,665 | -$90K | 0.409 | 155 |
| HIGH EDGE ONLY (edge=3.0) | $3,250 | -$26K | 1.255 | 65 |
| FAVORITES ONLY (FV>10) | $2,228 | -$15K | 1.467 | 54 |
| BUY ONLY | $1,681 | -$7K | 2.291 | 38 |
| SNIPER (edge=4.0, qty=3) | $1,284 | -$6K | 2.260 | 63 |

*Sharpe = MTM / |MaxLoss| * 10

### Grid Search (560 parameter combinations)
- **Best raw P&L**: edge=0.5, pos=100, kelly=0.10 -> $9,922 but -$284K max loss
- **Best risk-adjusted**: edge=4.0, pos=100, kelly=0.10 -> $2,814 on only -$12K max loss (Sharpe=2.245)
- **Key insight**: lowering min_edge from 1.5 to 1.0 increases MTM by ~34%, raising position limit from 80 to 100 adds ~23%

---

## 5. WHAT I NEED GEMINI TO RESEARCH

### 5A. Live Game Probability Models
Our current in-game model uses a simple Gaussian CDF with:
- `expected_margin = score_diff + (AdjEM_diff * time_remaining/2400)`
- `std_dev = 11.0 * sqrt(time_remaining/2400)`

**Research needed:**
1. What do state-of-the-art live basketball win probability models look like? (e.g., ESPN's own model, FiveThirtyEight's, Ken Pomeroy's)
2. Is our standard deviation of 11.0 correct for college basketball? Should it vary by game pace, team style, or tournament round?
3. How should we handle **overtime scenarios** in the probability model?
4. Do academic papers or sports analytics sources suggest better functional forms than Gaussian CDF for in-game win probability?
5. How do **possession count, timeout availability, and foul situation** affect late-game probabilities?
6. Is there a **momentum/hot-hand factor** we should incorporate when a team is on a scoring run?

### 5B. Betting Market Dynamics During Live Games
1. How do **sportsbook live odds** move during March Madness games? What patterns are predictable?
2. Is there a known **"halftime overreaction"** effect where the market overshoots on halftime scores?
3. How do **sharp bettors vs recreational bettors** behave differently during live games?
4. What is the typical **latency advantage** in live betting markets? (i.e., how fast do odds adjust to scoring events?)
5. Are there known **market inefficiencies** in live college basketball betting (e.g., the market being slow to adjust to tempo, foul trouble, key player injuries)?
6. How does **"closing line value" (CLV)** analysis apply to tournament futures contracts?

### 5C. Tournament-Specific Dynamics
1. How should fair values for remaining teams **adjust when an upset occurs** in a different region? (e.g., if a 1-seed loses in East, do West teams' values change?)
2. What is the historical **upset rate by seed matchup** in March Madness? (1v16, 2v15, etc.) Are our logistic model probabilities calibrated correctly?
3. How does **"chalk" vs "upset" bias** manifest in prediction markets? Do tournament markets systematically overprice or underprice longshots?
4. Is there a **"Cinderella premium"** - do fans/bettors irrationally bid up underdog contracts during the tournament?
5. How should we handle the **increasing variance** as the tournament progresses? (fewer games = more uncertainty about later rounds)

### 5D. Market Making & Microstructure Strategy
1. What are best practices for **market making in prediction markets** with settlement discontinuities (0/2/4/8/16/32/64)?
2. How should we set **bid-ask spreads** around our fair value? Should spread width scale with FV, volatility, or time to settlement?
3. We detected potential **wash trading** (artificial tight spreads far from FV). How should we:
   - Detect it more reliably?
   - Avoid getting trapped by it?
   - Exploit the wash trader's behavior?
4. Given the strong **mean-reversion** we observe (autocorrelation -0.3 to -0.9), what mean-reversion strategies work in prediction markets?
5. How should **inventory risk** be managed when we have large directional positions (e.g., short 80 contracts on a team)?

### 5E. Specific Model Improvements
1. Our model uses **static KenPom ratings**. Should we incorporate:
   - Injury reports / player availability?
   - Travel/rest advantages?
   - Historical tournament performance by coach?
   - Public sentiment/momentum from prior games?
2. The **logistic win probability function** (`1 / (1 + 10^(-diff/11))`) - is the scaling factor of 11.0 well-calibrated for NCAA tournament games specifically (vs regular season)?
3. Should we use a **different sigma for tournament games** vs regular season? Tournament games may have different variance due to pressure, neutral court, higher stakes.
4. Our **Option Value formula** is `p * (1-p) * (time_remaining/2400) * 6.4`. Is this a reasonable approximation? What do prediction market practitioners use?
5. We currently have **hard price boundaries at 6.4 and 57.6** (10% and 90% of max). Should these be dynamic?

### 5F. Optimal Execution
1. Given that we see **5-second order cooldown**, what is the optimal order placement strategy? Should we use:
   - IOC (immediate or cancel) orders?
   - Passive limit orders with regular repricing?
   - Iceberg/hidden orders if the platform supports them?
2. How should we **split large orders** to minimize market impact? (e.g., we sometimes buy 10x Duke at once)
3. What is the optimal **rebalancing frequency** for our portfolio during live games vs between games?

---

## 6. TOURNAMENT STATUS (as of March 20, 2026)

- **First Four**: Completed (March 18-19)
- **Round of 64**: Starting TODAY (March 20)
  - Day 1 (March 20): East + West regions (16 games)
  - Day 2 (March 21): South + Midwest regions (16 games)
- **Our positions playing today**: Long Duke(+30), Arizona(+36); Short Connecticut(-80), Michigan State(-66), UCLA(-71), St. John's(-38), Purdue(-28), Ohio State(-29), Louisville(-15), Wisconsin(-30), Arkansas(-30), Miami FL(-4), High Point(-1)
- **No teams eliminated yet** (tournament R1 starts today)

---

## 7. DESIRED OUTPUT FROM GEMINI

1. **Literature review** of the best live game probability models for college basketball
2. **Specific parameter recommendations** for our in-game model (sigma, functional form, adjustments)
3. **Betting market behavior patterns** during March Madness that we can exploit
4. **Market making strategy recommendations** tailored to this settlement structure
5. **Risk management recommendations** given our current portfolio
6. **Any known edges** in NCAA tournament prediction markets that we're not capturing
7. **Code-level suggestions** if possible (Python) for improving our `live_win_probability()` function and `_evaluate_and_trade()` logic
