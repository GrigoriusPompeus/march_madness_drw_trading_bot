"""
NCAA Tournament Fair Value Model
Computes expected settlement values for all 68 teams using Monte Carlo simulation
with KenPom-style adjusted efficiency margin ratings.

Settlement values:
  Champion: 64, Runner-up: 32, Final Four: 16, Elite Eight: 8,
  Sweet Sixteen: 4, Second Round: 2, First Round/First Four: 0
"""

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Team ratings (KenPom AdjEM estimates for 2025-26 season)
# Derived from public KenPom rank data, betting lines, and efficiency metrics
# ---------------------------------------------------------------------------

TEAM_RATINGS: Dict[str, float] = {
    # Source: Average of Sports Reference SRS + ESPN BPI (2025-26)
    # Top 10
    "Duke":           28.58,   # SRS 31.55, BPI 25.6
    "Michigan":       28.29,   # SRS 32.48, BPI 24.1
    "Arizona":        26.81,   # SRS 29.92, BPI 23.7
    "Florida":        25.10,   # SRS 27.90, BPI 22.3
    "Houston":        24.62,   # SRS 26.23, BPI 23.0
    "Iowa State":     24.33,   # SRS 27.15, BPI 21.5
    "Illinois":       23.73,   # SRS 26.45, BPI 21.0
    "Purdue":         23.12,   # SRS 25.64, BPI 20.6
    "Gonzaga":        22.91,   # SRS 25.11, BPI 20.7
    "Louisville":     21.21,   # SRS 23.41, BPI 19.0

    # 11-20
    "Connecticut":    21.16,   # SRS 22.92, BPI 19.4
    "Michigan State":  20.92,  # SRS 23.44, BPI 18.4
    "Vanderbilt":     20.52,   # SRS 22.83, BPI 18.2
    "Tennessee":      20.49,   # SRS 22.08, BPI 18.9
    "Alabama":        20.30,   # SRS 22.70, BPI 17.9
    "St. John's":     20.11,   # SRS 22.42, BPI 17.8
    "Arkansas":       19.67,   # SRS 22.43, BPI 16.9
    "Texas Tech":     19.28,   # SRS 21.95, BPI 16.6
    "Nebraska":       19.27,   # SRS 21.54, BPI 17.0
    "Virginia":       19.15,   # SRS 21.60, BPI 16.7

    # 21-30
    "BYU":            18.92,   # SRS 21.04, BPI 16.8
    "Kansas":         18.85,   # SRS 21.10, BPI 16.6
    "Kentucky":       18.15,   # SRS 19.80, BPI 16.5
    "Wisconsin":      17.66,   # SRS 19.52, BPI 15.8
    "Ohio State":     16.98,   # SRS 19.06, BPI 14.9
    "North Carolina":  16.90,  # SRS 19.39, BPI 14.4
    "Georgia":        16.84,   # SRS 19.57, BPI 14.1
    "UCLA":           16.74,   # SRS 18.28, BPI 15.2
    "Iowa":           16.70,   # SRS 19.00, BPI 14.4
    "Saint Mary's":   16.34,   # SRS 17.87, BPI 14.8

    # 31-40
    "Clemson":        15.80,   # SRS 16.99, BPI 14.6
    "NC State":       15.71,   # SRS 18.02, BPI 13.4 (eliminated)
    "Utah State":     15.73,   # SRS 17.36, BPI 14.1
    "Miami FL":       15.67,   # SRS 18.04, BPI 13.3
    "Saint Louis":    15.59,   # SRS 17.27, BPI 13.9
    "Texas A&M":      15.48,   # SRS 16.76, BPI 14.2
    "Villanova":      14.95,   # SRS 16.30, BPI 13.6
    "Texas":          14.89,   # SRS 16.17, BPI 13.6
    "SMU":            14.69,   # SRS 17.27, BPI 12.1 (eliminated)
    "TCU":            13.48,   # SRS 15.75, BPI 11.2

    # 41-50
    "Santa Clara":    13.39,   # SRS 15.88, BPI 10.9
    "UCF":            13.30,   # SRS 13.30 (BPI not in top 50)
    "South Florida":  13.29,   # SRS 13.29 (BPI not in top 50)
    "Missouri":       12.54,   # SRS 13.08, BPI 12.0
    "VCU":            12.45,   # SRS 13.19, BPI 11.7

    # 51-68 (mid-majors / lower seeds)
    # Akron & N. Iowa from SRS; rest estimated from conference strength
    "Akron":           9.04,   # SRS 9.04
    "Northern Iowa":   8.49,   # SRS 8.49
    "McNeese":         6.0,
    "Hofstra":         5.5,
    "High Point":      4.0,
    "Troy":            4.0,
    "Hawaii":          3.5,
    "Miami OH":        3.0,
    "Penn":            2.5,
    "Kennesaw State":  2.0,
    "North Dakota State": 1.5,
    "Cal Baptist":     0.5,
    "Wright State":    0.0,
    "Furman":         -1.0,
    "Queens":         -2.0,
    "Idaho":          -3.0,
    "Tennessee State": -4.0,
    "Siena":          -5.0,
    "Long Island":    -6.0,
    "Lehigh":         -7.0,    # eliminated
    "Prairie View A&M": -8.0,
    "Howard":         -9.0,
    "UMBC":          -10.0,    # eliminated
}


# ---------------------------------------------------------------------------
# Estimated possessions per game (tempo) for each team
# ---------------------------------------------------------------------------

TEAM_PACE: Dict[str, float] = {
    # High tempo (~74-76)
    "Duke": 76.0,
    "Arizona": 74.0,
    "Florida": 75.0,
    "Michigan": 73.0,
    "Alabama": 74.0,
    "Gonzaga": 74.0,
    "Arkansas": 73.0,
    "North Carolina": 73.0,
    "Iowa": 72.0,
    "Michigan State": 72.0,

    # Medium tempo (~68-72)
    "Connecticut": 71.0,
    "Kansas": 71.0,
    "Kentucky": 71.0,
    "Vanderbilt": 70.0,
    "Iowa State": 70.0,
    "Louisville": 70.0,
    "BYU": 70.0,
    "Georgia": 70.0,
    "Illinois": 69.0,
    "Nebraska": 69.0,
    "Ohio State": 69.0,
    "Missouri": 69.0,
    "Houston": 68.0,
    "St. John's": 68.0,
    "UCLA": 68.0,
    "Miami FL": 68.0,
    "Villanova": 68.0,

    # Lower tempo (~64-67)
    "Purdue": 67.0,
    "Clemson": 67.0,
    "TCU": 67.0,
    "Tennessee": 66.0,
    "Texas Tech": 66.0,
    "Wisconsin": 64.0,

    # Low tempo (~62-63)
    "Saint Mary's": 63.0,
    "Virginia": 62.0,
}


def get_team_pace(team_name: str) -> float:
    """Look up estimated possessions per game for a team. Default 68.0."""
    return TEAM_PACE.get(team_name, 68.0)


# ---------------------------------------------------------------------------
# 2026 NCAA Tournament Bracket Structure
# ---------------------------------------------------------------------------

@dataclass
class Matchup:
    """A single tournament matchup."""
    team_a: str
    seed_a: int
    team_b: str
    seed_b: int


# First Four play-in games (winners advance to R1)
FIRST_FOUR = [
    Matchup("Prairie View A&M", 16, "Lehigh", 16),       # South 16 seed
    Matchup("Miami OH", 11, "SMU", 11),                   # Midwest 11 seed
    Matchup("Texas", 11, "NC State", 11),                  # West 11 seed
    Matchup("UMBC", 16, "Howard", 16),                     # Midwest 16 seed
]

# Bracket by region - ordered as 1v16, 8v9, 5v12, 4v13, 6v11, 3v14, 7v10, 2v15
# This ordering determines the bracket tree (1/16 winner plays 8/9 winner, etc.)

EAST_BRACKET = [
    Matchup("Duke", 1, "Siena", 16),
    Matchup("Ohio State", 8, "TCU", 9),
    Matchup("St. John's", 5, "Northern Iowa", 12),
    Matchup("Kansas", 4, "Cal Baptist", 13),
    Matchup("Louisville", 6, "South Florida", 11),
    Matchup("Michigan State", 3, "North Dakota State", 14),
    Matchup("UCLA", 7, "UCF", 10),
    Matchup("Connecticut", 2, "Furman", 15),
]

WEST_BRACKET = [
    Matchup("Arizona", 1, "Long Island", 16),
    Matchup("Villanova", 8, "Utah State", 9),
    Matchup("Wisconsin", 5, "High Point", 12),
    Matchup("Arkansas", 4, "Hawaii", 13),
    Matchup("BYU", 6, "FF_WEST_11", 11),                  # First Four winner
    Matchup("Gonzaga", 3, "Kennesaw State", 14),
    Matchup("Miami FL", 7, "Missouri", 10),
    Matchup("Purdue", 2, "Queens", 15),
]

SOUTH_BRACKET = [
    Matchup("Florida", 1, "FF_SOUTH_16", 16),             # First Four winner
    Matchup("Clemson", 8, "Iowa", 9),
    Matchup("Vanderbilt", 5, "McNeese", 12),
    Matchup("Nebraska", 4, "Troy", 13),
    Matchup("North Carolina", 6, "VCU", 11),
    Matchup("Illinois", 3, "Penn", 14),
    Matchup("Saint Mary's", 7, "Texas A&M", 10),
    Matchup("Houston", 2, "Idaho", 15),
]

MIDWEST_BRACKET = [
    Matchup("Michigan", 1, "FF_MIDWEST_16", 16),          # First Four winner
    Matchup("Georgia", 8, "Saint Louis", 9),
    Matchup("Texas Tech", 5, "Akron", 12),
    Matchup("Alabama", 4, "Hofstra", 13),
    Matchup("Tennessee", 6, "FF_MIDWEST_11", 11),          # First Four winner
    Matchup("Virginia", 3, "Wright State", 14),
    Matchup("Kentucky", 7, "Santa Clara", 10),
    Matchup("Iowa State", 2, "Tennessee State", 15),
]


# Settlement values per round reached
SETTLEMENT = {
    "R1_EXIT": 0,       # Lost in first round (or First Four)
    "R2_EXIT": 2,       # Lost in second round
    "S16_EXIT": 4,      # Lost in Sweet 16
    "E8_EXIT": 8,       # Lost in Elite Eight
    "F4_EXIT": 16,      # Lost in Final Four
    "RUNNER_UP": 32,    # Lost in Championship
    "CHAMPION": 64,     # Won Championship
}


def win_probability(rating_a: float, rating_b: float) -> float:
    """
    Compute P(A beats B) using logistic model on AdjEM difference.
    Calibrated so that ~5 point AdjEM gap ≈ 73% win probability.
    """
    diff = rating_a - rating_b
    return 1.0 / (1.0 + 10.0 ** (-diff / 11.0))

def live_win_probability(
    rating_a: float,
    rating_b: float,
    score_diff: int,                # A's score - B's score
    time_remaining_seconds: float,
    expected_pace: float = 68.0,    # projected possessions per game
    timeouts_trailing: int = 0,     # trailing team's remaining timeouts
) -> float:
    """
    Compute real-time P(A beats B) considering live score, game clock,
    pace, and trailing-team timeouts.

    Enhancements over naive model:
      - Pace-adjusted standard deviation (faster games -> wider variance).
      - Non-linear (sqrt) time scaling for variance to capture increased
        volatility in mid-game swings.
      - Late-game timeout extension: under 4 min, each trailing-team
        timeout adds ~15 sec of effective game time.
      - Overtime boundary: tied game at buzzer reverts toward pregame edge.
    """
    pregame_prob = win_probability(rating_a, rating_b)

    # --- Terminal / overtime boundary ---
    if time_remaining_seconds <= 0:
        if score_diff > 0:
            return 1.0
        elif score_diff < 0:
            return 0.0
        else:
            # Tied at end of regulation / OT: slight edge to better team
            return 0.5 + 0.69 * (pregame_prob - 0.5)

    # --- Effective time (timeout extension in late game) ---
    effective_time = time_remaining_seconds
    if time_remaining_seconds < 240.0 and score_diff != 0:
        effective_time += timeouts_trailing * 15.0

    # --- Time factors ---
    time_factor = effective_time / 2400.0          # linear fraction of game
    sqrt_time_factor = math.sqrt(time_factor)      # non-linear for variance

    # --- Expected margin (linear time scaling) ---
    expected_margin = score_diff + (rating_a - rating_b) * time_factor

    # --- Pace-adjusted, sqrt-scaled standard deviation ---
    sigma_adj = 11.0 * math.sqrt(expected_pace / 68.0)
    variance_sd = sigma_adj * sqrt_time_factor

    if variance_sd == 0:
        return 1.0 if expected_margin > 0 else (0.0 if expected_margin < 0 else 0.5)

    # --- Gaussian CDF ---
    return 0.5 * (1.0 + math.erf(expected_margin / (variance_sd * math.sqrt(2.0))))


FIRST_FOUR_GAMES = [
    ("FF_SOUTH_16", "Prairie View A&M", "Lehigh"),
    ("FF_MIDWEST_11", "Miami OH", "SMU"),
    ("FF_WEST_11", "Texas", "NC State"),
    ("FF_MIDWEST_16", "UMBC", "Howard"),
]


def resolve_first_four(eliminated: Optional[set] = None, live_games: Optional[Dict] = None) -> Dict[str, str]:
    """Simulate First Four games and return mapping of placeholder -> winner."""
    if eliminated is None:
        eliminated = set()
    if live_games is None:
        live_games = {}
    results = {}

    for placeholder, team_a, team_b in FIRST_FOUR_GAMES:
        a_elim = team_a in eliminated
        b_elim = team_b in eliminated

        if a_elim and not b_elim:
            winner = team_b
        elif b_elim and not a_elim:
            winner = team_a
        else:
            rating_a = TEAM_RATINGS.get(team_a, 0.0)
            rating_b = TEAM_RATINGS.get(team_b, 0.0)
            if team_a in live_games and live_games[team_a]["opponent"] == team_b:
                lg = live_games[team_a]
                p = live_win_probability(rating_a, rating_b, lg["score_diff"], lg["time_remaining"])
            elif team_b in live_games and live_games[team_b]["opponent"] == team_a:
                lg = live_games[team_b]
                p = live_win_probability(rating_a, rating_b, -lg["score_diff"], lg["time_remaining"])
            else:
                p = win_probability(rating_a, rating_b)
            winner = team_a if random.random() < p else team_b

        results[placeholder] = winner

    return results


def simulate_round(
    matchups: List[Tuple[str, str]],
    eliminated: Optional[set] = None,
    live_games: Optional[Dict] = None,
) -> List[str]:
    """Simulate a round of games. Returns list of winners."""
    if eliminated is None:
        eliminated = set()
    if live_games is None:
        live_games = {}
    winners = []
    for team_a, team_b in matchups:
        # If one team is eliminated, the other wins automatically
        a_elim = team_a in eliminated
        b_elim = team_b in eliminated
        if a_elim and not b_elim:
            winners.append(team_b)
        elif b_elim and not a_elim:
            winners.append(team_a)
        elif a_elim and b_elim:
            # Both eliminated? Shouldn't happen, pick randomly
            winners.append(team_a)
        else:
            rating_a = TEAM_RATINGS.get(team_a, 0.0)
            rating_b = TEAM_RATINGS.get(team_b, 0.0)
            if team_a in live_games and live_games[team_a]["opponent"] == team_b:
                lg = live_games[team_a]
                p = live_win_probability(rating_a, rating_b, lg["score_diff"], lg["time_remaining"])
            elif team_b in live_games and live_games[team_b]["opponent"] == team_a:
                lg = live_games[team_b]
                p = live_win_probability(rating_a, rating_b, -lg["score_diff"], lg["time_remaining"])
            else:
                p = win_probability(rating_a, rating_b)
            winner = team_a if random.random() < p else team_b
            winners.append(winner)
    return winners


def simulate_region(
    bracket: List[Matchup],
    ff_results: Dict[str, str],
    eliminated: Optional[set] = None,
    live_games: Optional[Dict] = None,
) -> Tuple[str, Dict[str, int]]:
    """
    Simulate a full regional bracket.
    Returns (regional_winner, settlement_mapping).
    """
    if eliminated is None:
        eliminated = set()
    settlements: Dict[str, int] = {}

    # Resolve First Four placeholders
    r1_matchups = []
    for m in bracket:
        team_a = ff_results.get(m.team_a, m.team_a)
        team_b = ff_results.get(m.team_b, m.team_b)
        r1_matchups.append((team_a, team_b))

    # Track all teams
    all_teams = set()
    for a, b in r1_matchups:
        all_teams.add(a)
        all_teams.add(b)

    # Round 1 (R64 -> R32)
    r1_winners = simulate_round(r1_matchups, eliminated, live_games)
    for a, b in r1_matchups:
        for team in [a, b]:
            if team not in r1_winners:
                settlements[team] = 0  # R1 exit

    # Round 2 (R32 -> S16): pair up winners in bracket order
    r2_matchups = [(r1_winners[i], r1_winners[i + 1]) for i in range(0, len(r1_winners), 2)]
    r2_winners = simulate_round(r2_matchups, eliminated, live_games)
    for a, b in r2_matchups:
        for team in [a, b]:
            if team not in r2_winners:
                settlements[team] = 2  # R2 exit

    # Sweet 16 -> Elite 8
    s16_matchups = [(r2_winners[i], r2_winners[i + 1]) for i in range(0, len(r2_winners), 2)]
    s16_winners = simulate_round(s16_matchups, eliminated, live_games)
    for a, b in s16_matchups:
        for team in [a, b]:
            if team not in s16_winners:
                settlements[team] = 4  # S16 exit

    # Elite 8 -> Final Four
    e8_matchups = [(s16_winners[0], s16_winners[1])]
    e8_winners = simulate_round(e8_matchups, eliminated, live_games)
    for a, b in e8_matchups:
        for team in [a, b]:
            if team not in e8_winners:
                settlements[team] = 8  # E8 exit

    regional_winner = e8_winners[0]
    # Regional winner continues - settlement determined in Final Four
    return regional_winner, settlements


def simulate_tournament(
    n_sims: int = 50000,
    eliminated: Optional[set] = None,
    live_games: Optional[Dict] = None,
) -> Dict[str, float]:
    """
    Run Monte Carlo simulation of the full tournament.
    Returns expected settlement value for each team.
    If eliminated set is provided, those teams always lose immediately.
    """
    if eliminated is None:
        eliminated = set()

    # Accumulate total settlement per team
    totals: Dict[str, float] = {}
    team_count: Dict[str, int] = {}

    for _ in range(n_sims):
        ff_results = resolve_first_four(eliminated, live_games)

        # Mark First Four losers
        sim_settlements: Dict[str, int] = {}
        ff_losers = set()
        for placeholder, team_a, team_b in FIRST_FOUR_GAMES:
            winner = ff_results.get(placeholder)
            if winner:
                loser = team_b if winner == team_a else team_a
                sim_settlements[loser] = 0
                ff_losers.add(loser)

        # Simulate each region
        regions = [
            ("East", EAST_BRACKET),
            ("West", WEST_BRACKET),
            ("South", SOUTH_BRACKET),
            ("Midwest", MIDWEST_BRACKET),
        ]

        final_four = []
        for region_name, bracket in regions:
            regional_winner, region_settlements = simulate_region(
                bracket, ff_results, eliminated, live_games
            )
            sim_settlements.update(region_settlements)
            final_four.append(regional_winner)

        # Final Four: East vs West, South vs Midwest
        sf1 = (final_four[0], final_four[1])  # East vs West
        sf2 = (final_four[2], final_four[3])  # South vs Midwest

        sf1_winner = simulate_round([sf1], eliminated, live_games)[0]
        sf1_loser = sf1[1] if sf1_winner == sf1[0] else sf1[0]
        sim_settlements[sf1_loser] = 16  # F4 exit

        sf2_winner = simulate_round([sf2], eliminated, live_games)[0]
        sf2_loser = sf2[1] if sf2_winner == sf2[0] else sf2[0]
        sim_settlements[sf2_loser] = 16  # F4 exit

        # Championship
        champ_winner = simulate_round([(sf1_winner, sf2_winner)], eliminated, live_games)[0]
        champ_loser = sf2_winner if champ_winner == sf1_winner else sf1_winner
        sim_settlements[champ_loser] = 32   # Runner-up
        sim_settlements[champ_winner] = 64  # Champion

        # Accumulate
        for team, value in sim_settlements.items():
            totals[team] = totals.get(team, 0.0) + value
            team_count[team] = team_count.get(team, 0) + 1

    # Compute averages
    expected_values: Dict[str, float] = {}
    for team in totals:
        expected_values[team] = totals[team] / team_count[team]

    return expected_values


def compute_fair_values(
    n_sims: int = 50000,
    eliminated: Optional[set] = None,
    live_games: Optional[Dict] = None,
) -> Dict[str, float]:
    """
    Compute fair values and return sorted by value descending.
    If eliminated is provided, those teams are forced to lose immediately.
    """
    ev = simulate_tournament(n_sims, eliminated=eliminated, live_games=live_games)
    return dict(sorted(ev.items(), key=lambda x: x[1], reverse=True))


# Map team names to likely DRW display symbols
# The exchange may use abbreviations - this mapping will be updated at runtime
TEAM_TO_SYMBOL: Dict[str, str] = {}
SYMBOL_TO_TEAM: Dict[str, str] = {}


def update_symbol_mapping(exchange_symbols: List[str]) -> None:
    """
    Given the list of display_symbols from the exchange orderbook,
    try to match them to our team names.
    """
    global TEAM_TO_SYMBOL, SYMBOL_TO_TEAM
    TEAM_TO_SYMBOL.clear()
    SYMBOL_TO_TEAM.clear()

    # Exchange symbol -> Expected Team Name override
    manual_overrides = {
        'UConn': 'Connecticut',
        'St Johns': "St. John's",
        'Michigan St': 'Michigan State',
        'Iowa St': 'Iowa State',
        'Ohio St': 'Ohio State',
        'Utah St': 'Utah State',
        'Tennessee St': 'Tennessee State',
        'Kennesaw St': 'Kennesaw State',
        'Wright St': 'Wright State',
        'North Dakota St': 'North Dakota State',
        'NC State': 'NC State',
        'Prairie View': 'Prairie View A&M',
        'Saint Marys': "Saint Mary's",
    }

    all_teams = list(TEAM_RATINGS.keys())

    for symbol in exchange_symbols:
        if symbol in manual_overrides:
            team = manual_overrides[symbol]
            TEAM_TO_SYMBOL[team] = symbol
            SYMBOL_TO_TEAM[symbol] = team
            continue
            
        sym_lower = symbol.lower().replace("-", " ").replace("_", " ")

        # Try exact match first
        for team in all_teams:
            if team.lower() == sym_lower:
                TEAM_TO_SYMBOL[team] = symbol
                SYMBOL_TO_TEAM[symbol] = team
                break
        else:
            # Try partial match
            for team in all_teams:
                team_parts = team.lower().split()
                if any(part in sym_lower for part in team_parts if len(part) > 3):
                    if team not in TEAM_TO_SYMBOL:
                        TEAM_TO_SYMBOL[team] = symbol
                        SYMBOL_TO_TEAM[symbol] = team
                        break


if __name__ == "__main__":
    print("Computing fair values (50,000 simulations)...")
    fair_values = compute_fair_values(50000)
    print(f"\n{'Team':<25} {'Fair Value':>10}  {'Implied Champion%':>18}")
    print("-" * 58)
    total = 0.0
    for team, fv in fair_values.items():
        champ_pct = 0.0  # approximate from fair value
        print(f"{team:<25} {fv:>10.2f}")
        total += fv
    print(f"\n{'TOTAL':<25} {total:>10.2f}  (should be ~224)")
