"""
Stress test: Validate team name mapping across ALL API integrations.
Tests for substring collisions (Michigan/Michigan State), missing mappings,
and formula correctness.
"""

import re
import sys

# ── Import all mapping code ──────────────────────────────────────────────────
from model import (
    TEAM_RATINGS, TEAM_PACE, TEAM_TO_SYMBOL, SYMBOL_TO_TEAM,
    update_symbol_mapping, win_probability, live_win_probability,
    get_effective_rating, get_team_pace,
    EAST_BRACKET, WEST_BRACKET, SOUTH_BRACKET, MIDWEST_BRACKET,
    FIRST_FOUR_GAMES, SETTLEMENT,
)
from live_data import ESPN_TO_MODEL, espn_to_model_name
from odds_api import (
    ODDS_API_TO_MODEL, resolve_team_name,
    american_to_implied_prob, decimal_to_implied_prob,
    devig_two_way, devig_multi_way, prob_to_adjem_diff,
)

PASS = 0
FAIL = 0
WARN = 0


def check(condition, msg, warn_only=False):
    global PASS, FAIL, WARN
    if condition:
        PASS += 1
    elif warn_only:
        WARN += 1
        print(f"  [WARN] {msg}")
    else:
        FAIL += 1
        print(f"  [FAIL] {msg}")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. SUBSTRING COLLISION TESTS
# ═══════════════════════════════════════════════════════════════════════════════
print("=" * 70)
print("1. SUBSTRING COLLISION TESTS")
print("=" * 70)

# Known dangerous pairs where one team name is a substring of another
COLLISION_PAIRS = [
    ("Michigan", "Michigan State"),
    ("Iowa", "Iowa State"),
    ("Tennessee", "Tennessee State"),
    ("Ohio State", "NC State"),   # "State" substring
    ("Miami FL", "Miami OH"),
    ("North Carolina", "North Dakota State"),
    ("Utah State", "Wright State"),   # "State" suffix
    ("Kennesaw State", "NC State"),
    ("Penn", "Prairie View A&M"),   # no collision expected
    ("Virginia", "West Virginia"),   # West Virginia not in tourney but test regex
    ("Long Island", "Rhode Island"),  # Rhode Island not in tourney
    ("Saint Mary's", "Saint Louis"),
    ("Cal Baptist", "California Baptist"),  # alias test
]

print("\n--- resolve_team_name (Odds API) ---")
# Test that full API names resolve correctly
odds_api_collision_tests = [
    ("Michigan Wolverines", "Michigan"),
    ("Michigan State Spartans", "Michigan State"),
    ("Iowa Hawkeyes", "Iowa"),
    ("Iowa State Cyclones", "Iowa State"),
    ("Tennessee Volunteers", "Tennessee"),
    ("Tennessee State Tigers", "Tennessee State"),
    ("Miami Hurricanes", "Miami FL"),
    ("Miami (OH) RedHawks", "Miami OH"),
    ("Miami OH RedHawks", "Miami OH"),
    ("North Carolina Tar Heels", "North Carolina"),
    ("North Dakota State Bison", "North Dakota State"),
    ("Ohio State Buckeyes", "Ohio State"),
    ("NC State Wolfpack", "NC State"),
    ("Utah State Aggies", "Utah State"),
    ("Saint Mary's Gaels", "Saint Mary's"),
    ("Saint Louis Billikens", "Saint Louis"),
    ("UConn Huskies", "Connecticut"),
    ("Connecticut Huskies", "Connecticut"),
    ("Cal Baptist Lancers", "Cal Baptist"),
    ("California Baptist Lancers", "Cal Baptist"),
    ("LIU Sharks", "Long Island"),
    ("Long Island University Sharks", "Long Island"),
    ("BYU Cougars", "BYU"),
    ("Brigham Young Cougars", "BYU"),
    ("Hawai'i Rainbow Warriors", "Hawaii"),
    ("Hawaii Rainbow Warriors", "Hawaii"),
    ("Penn Quakers", "Penn"),
    ("Pennsylvania Quakers", "Penn"),
]

for api_name, expected in odds_api_collision_tests:
    result = resolve_team_name(api_name)
    check(result == expected, f"resolve_team_name('{api_name}') = '{result}', expected '{expected}'")

# Test short/partial names that might cause collisions
print("\n--- Short name collision tests (resolve_team_name) ---")
short_name_tests = [
    ("Michigan", "Michigan"),         # Should NOT match "Michigan State"
    ("Iowa", "Iowa"),                 # Should NOT match "Iowa State"
    ("Tennessee", "Tennessee"),       # Should NOT match "Tennessee State"
    ("Miami FL", "Miami FL"),
    ("Miami OH", "Miami OH"),
    ("Duke", "Duke"),
    ("NC State", "NC State"),
    ("Ohio State", "Ohio State"),
    ("Penn", "Penn"),
]

for name, expected in short_name_tests:
    result = resolve_team_name(name)
    check(result == expected, f"resolve_team_name('{name}') = '{result}', expected '{expected}'")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. ESPN NAME MAPPING TESTS
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("2. ESPN NAME MAPPING TESTS")
print("=" * 70)

espn_collision_tests = [
    ("Michigan Wolverines", "Michigan"),
    ("Michigan State Spartans", "Michigan State"),
    ("Iowa Hawkeyes", "Iowa"),
    ("Iowa State Cyclones", "Iowa State"),
    ("Tennessee Volunteers", "Tennessee"),
    ("Tennessee State Tigers", "Tennessee State"),
    ("Miami Hurricanes", "Miami FL"),
    ("Miami (OH) RedHawks", "Miami OH"),
    ("North Carolina Tar Heels", "North Carolina"),
    ("North Dakota State Bison", "North Dakota State"),
    ("Ohio State Buckeyes", "Ohio State"),
    ("NC State Wolfpack", "NC State"),
    ("UConn Huskies", "Connecticut"),
    ("Connecticut Huskies", "Connecticut"),
    ("Cal Baptist Lancers", "Cal Baptist"),
    ("Saint Mary's Gaels", "Saint Mary's"),
    ("Saint Louis Billikens", "Saint Louis"),
]

for espn_name, expected in espn_collision_tests:
    result = espn_to_model_name(espn_name)
    check(result == expected, f"espn_to_model_name('{espn_name}') = '{result}', expected '{expected}'")

# Test that every team in TEAM_RATINGS has an ESPN mapping path
print("\n--- ESPN coverage check ---")
espn_model_values = set(ESPN_TO_MODEL.values())
for team in TEAM_RATINGS:
    check(team in espn_model_values, f"Team '{team}' missing from ESPN_TO_MODEL values", warn_only=True)

# Check ESPN dict has the "California Baptist" variant
check("California Baptist Lancers" in ESPN_TO_MODEL or "Cal Baptist Lancers" in ESPN_TO_MODEL,
      "ESPN mapping missing California Baptist / Cal Baptist variant")


# ═══════════════════════════════════════════════════════════════════════════════
# 3. EXCHANGE SYMBOL MAPPING TESTS
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("3. EXCHANGE SYMBOL MAPPING TESTS")
print("=" * 70)

# Simulate the actual exchange symbols (from bot.py _aggressive_match)
EXCHANGE_SYMBOLS = [
    "Akron", "Alabama", "Arizona", "Arkansas", "BYU", "Cal Baptist",
    "Clemson", "Duke", "Florida", "Furman", "Georgia", "Gonzaga",
    "Hawaii", "High Point", "Hofstra", "Houston", "Howard", "Idaho",
    "Illinois", "Iowa", "Iowa St", "Kansas", "Kennesaw St",
    "Kentucky", "Lehigh", "Long Island", "Louisville", "McNeese",
    "Miami FL", "Miami OH", "Michigan", "Michigan St", "Missouri",
    "NC State", "Nebraska", "North Carolina", "North Dakota St",
    "Northern Iowa", "Ohio St", "Penn", "Prairie View", "Purdue",
    "Queens", "SMU", "Saint Louis", "Saint Marys", "Santa Clara",
    "Siena", "South Florida", "St Johns", "TCU", "Tennessee",
    "Tennessee St", "Texas", "Texas A&M", "Texas Tech", "Troy",
    "UCF", "UCLA", "UConn", "UMBC", "Utah St", "VCU", "Vanderbilt",
    "Villanova", "Virginia", "Wisconsin", "Wright St",
]

update_symbol_mapping(EXCHANGE_SYMBOLS)

# Critical collision tests for exchange symbols
exchange_collision_tests = [
    ("Michigan", "Michigan"),
    ("Michigan St", "Michigan State"),
    ("Iowa", "Iowa"),
    ("Iowa St", "Iowa State"),
    ("Tennessee", "Tennessee"),
    ("Tennessee St", "Tennessee State"),
    ("Ohio St", "Ohio State"),
    ("NC State", "NC State"),
    ("Miami FL", "Miami FL"),
    ("Miami OH", "Miami OH"),
    ("UConn", "Connecticut"),
    ("St Johns", "St. John's"),
    ("Saint Marys", "Saint Mary's"),
    ("Prairie View", "Prairie View A&M"),
    ("Utah St", "Utah State"),
    ("North Dakota St", "North Dakota State"),
    ("Kennesaw St", "Kennesaw State"),
    ("Wright St", "Wright State"),
]

for symbol, expected_team in exchange_collision_tests:
    actual = SYMBOL_TO_TEAM.get(symbol)
    check(actual == expected_team, f"SYMBOL_TO_TEAM['{symbol}'] = '{actual}', expected '{expected_team}'")

# Check every exchange symbol maps to something
unmapped = [s for s in EXCHANGE_SYMBOLS if s not in SYMBOL_TO_TEAM]
check(len(unmapped) == 0, f"Unmapped exchange symbols: {unmapped}")

# Check no two symbols map to the same team
team_to_syms = {}
for sym, team in SYMBOL_TO_TEAM.items():
    if team not in team_to_syms:
        team_to_syms[team] = []
    team_to_syms[team].append(sym)
for team, syms in team_to_syms.items():
    check(len(syms) == 1, f"Team '{team}' mapped from multiple symbols: {syms}", warn_only=True)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. KALSHI TITLE PARSING TESTS
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("4. KALSHI TITLE PARSING SIMULATION")
print("=" * 70)

# Simulate Kalshi title parsing (from _parse_markets)
def simulate_kalshi_title_match(title):
    """Simulate the Kalshi _parse_markets team extraction."""
    for team in sorted(TEAM_RATINGS.keys(), key=len, reverse=True):
        if re.search(r'\b' + re.escape(team.lower()) + r'\b', title.lower()):
            return team
    return None

kalshi_title_tests = [
    ("Will Michigan win the NCAA Tournament?", "Michigan"),
    ("Will Michigan State reach the Final Four?", "Michigan State"),
    ("Michigan State to win Championship", "Michigan State"),
    ("Iowa State: Elite Eight", "Iowa State"),
    ("Will Iowa win their next game?", "Iowa"),
    ("Tennessee State championship odds", "Tennessee State"),
    ("Tennessee to reach Final Four", "Tennessee"),
    ("Will Miami FL advance to Sweet 16?", "Miami FL"),
    ("Miami OH tournament odds", "Miami OH"),
    ("North Carolina vs Duke", "North Carolina"),
    ("North Dakota State upset chances", "North Dakota State"),
    ("Will NC State make the tournament?", "NC State"),
    ("Ohio State basketball odds", "Ohio State"),
    ("Saint Mary's to advance", "Saint Mary's"),
    ("Saint Louis upset potential", "Saint Louis"),
    ("Will St. John's win?", "St. John's"),
    ("Prairie View A&M chances", "Prairie View A&M"),
    ("Connecticut Huskies championship", "Connecticut"),
]

for title, expected in kalshi_title_tests:
    result = simulate_kalshi_title_match(title)
    check(result == expected, f"Kalshi title '{title[:50]}...' -> '{result}', expected '{expected}'")


# ═══════════════════════════════════════════════════════════════════════════════
# 5. FORMULA VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("5. FORMULA VALIDATION")
print("=" * 70)

# Win probability
print("\n--- win_probability ---")
# Equal teams -> 50%
p = win_probability(20.0, 20.0)
check(abs(p - 0.5) < 0.001, f"Equal teams: P={p:.4f}, expected 0.5000")

# Much better team -> close to 1.0
p = win_probability(30.0, 0.0)
check(p > 0.95, f"30 vs 0: P={p:.4f}, expected > 0.95")

# Much worse team -> close to 0.0
p = win_probability(0.0, 30.0)
check(p < 0.05, f"0 vs 30: P={p:.4f}, expected < 0.05")

# Symmetry
p1 = win_probability(25.0, 15.0)
p2 = win_probability(15.0, 25.0)
check(abs(p1 + p2 - 1.0) < 0.001, f"Symmetry: P1={p1:.4f} + P2={p2:.4f} = {p1+p2:.4f}")

# Live win probability
print("\n--- live_win_probability ---")
# Game over, team A winning
p = live_win_probability(20.0, 20.0, score_diff=10, time_remaining_seconds=0)
check(p == 1.0, f"Game over +10: P={p:.4f}, expected 1.0")

# Game over, team A losing
p = live_win_probability(20.0, 20.0, score_diff=-5, time_remaining_seconds=0)
check(p == 0.0, f"Game over -5: P={p:.4f}, expected 0.0")

# Game over, tied (equal teams)
p = live_win_probability(20.0, 20.0, score_diff=0, time_remaining_seconds=0)
check(abs(p - 0.5) < 0.01, f"Game over tied equal: P={p:.4f}, expected ~0.5")

# Game over, tied (better team gets edge)
p = live_win_probability(30.0, 10.0, score_diff=0, time_remaining_seconds=0)
check(p > 0.5, f"Game over tied better team: P={p:.4f}, expected > 0.5")

# Halftime, big lead
p = live_win_probability(20.0, 20.0, score_diff=20, time_remaining_seconds=1200)
check(p > 0.9, f"Halftime +20: P={p:.4f}, expected > 0.9")

# Full game remaining, no score difference
p = live_win_probability(25.0, 15.0, score_diff=0, time_remaining_seconds=2400)
p_pre = win_probability(25.0, 15.0)
# Live uses Gaussian CDF, pregame uses logistic - they diverge at large diffs.
# Allow 0.10 tolerance (different models, not a bug)
check(abs(p - p_pre) < 0.10, f"Full game 0-0: live P={p:.4f} vs pregame P={p_pre:.4f}, expected within 0.10")

# Pace adjustment test: fast game -> wider variance
p_fast = live_win_probability(20.0, 20.0, score_diff=5, time_remaining_seconds=600, expected_pace=80.0)
p_slow = live_win_probability(20.0, 20.0, score_diff=5, time_remaining_seconds=600, expected_pace=60.0)
# Faster pace = more variance = less certainty for the leader
check(p_fast < p_slow, f"Pace effect: fast game P={p_fast:.4f} < slow game P={p_slow:.4f} (more variance)")

# Odds conversion
print("\n--- Odds conversions ---")
# -200 American = 66.7% implied
p = american_to_implied_prob(-200)
check(abs(p - 0.6667) < 0.01, f"-200 American: {p:.4f}, expected ~0.667")

# +150 American = 40% implied
p = american_to_implied_prob(150)
check(abs(p - 0.4) < 0.01, f"+150 American: {p:.4f}, expected ~0.400")

# Devig test
h_raw, a_raw = american_to_implied_prob(-150), american_to_implied_prob(130)
h_true, a_true = devig_two_way(h_raw, a_raw)
check(abs(h_true + a_true - 1.0) < 0.001, f"Devig sums to 1: {h_true:.4f} + {a_true:.4f} = {h_true+a_true:.4f}")

# prob_to_adjem_diff round-trip
for diff in [-20, -10, -5, 0, 5, 10, 20]:
    p = win_probability(diff, 0.0)  # P(A beats B) where A has rating=diff, B has rating=0
    recovered = prob_to_adjem_diff(p)
    check(abs(recovered - diff) < 0.1, f"Round-trip diff={diff}: prob={p:.4f} -> recovered={recovered:.2f}")


# ═══════════════════════════════════════════════════════════════════════════════
# 6. BRACKET INTEGRITY
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("6. BRACKET INTEGRITY")
print("=" * 70)

# Check all teams in brackets exist in TEAM_RATINGS
all_bracket_teams = set()
ff_placeholders = {ph for ph, _, _ in FIRST_FOUR_GAMES}

for bracket_name, bracket in [("East", EAST_BRACKET), ("West", WEST_BRACKET),
                                ("South", SOUTH_BRACKET), ("Midwest", MIDWEST_BRACKET)]:
    for m in bracket:
        for team in [m.team_a, m.team_b]:
            if team not in ff_placeholders:
                all_bracket_teams.add(team)
                check(team in TEAM_RATINGS, f"Bracket team '{team}' ({bracket_name}) not in TEAM_RATINGS")

for _, team_a, team_b in FIRST_FOUR_GAMES:
    all_bracket_teams.add(team_a)
    all_bracket_teams.add(team_b)
    check(team_a in TEAM_RATINGS, f"First Four team '{team_a}' not in TEAM_RATINGS")
    check(team_b in TEAM_RATINGS, f"First Four team '{team_b}' not in TEAM_RATINGS")

# Check TEAM_RATINGS has 68 teams
check(len(TEAM_RATINGS) == 68, f"TEAM_RATINGS has {len(TEAM_RATINGS)} teams, expected 68")

# Check all bracket teams are unique (no team appears twice)
bracket_team_list = []
for bracket in [EAST_BRACKET, WEST_BRACKET, SOUTH_BRACKET, MIDWEST_BRACKET]:
    for m in bracket:
        for team in [m.team_a, m.team_b]:
            if team not in ff_placeholders:
                bracket_team_list.append(team)
for _, team_a, team_b in FIRST_FOUR_GAMES:
    bracket_team_list.append(team_a)
    bracket_team_list.append(team_b)

from collections import Counter
dupes = {t: c for t, c in Counter(bracket_team_list).items() if c > 1}
check(len(dupes) == 0, f"Duplicate teams in bracket: {dupes}")

# Check each region has exactly 8 matchups
for name, bracket in [("East", EAST_BRACKET), ("West", WEST_BRACKET),
                       ("South", SOUTH_BRACKET), ("Midwest", MIDWEST_BRACKET)]:
    check(len(bracket) == 8, f"{name} has {len(bracket)} matchups, expected 8")


# ═══════════════════════════════════════════════════════════════════════════════
# 7. CROSS-API CONSISTENCY
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("7. CROSS-API CONSISTENCY")
print("=" * 70)

# Every team in TEAM_RATINGS should be reachable from all mapping systems
odds_model_values = set(ODDS_API_TO_MODEL.values())
espn_model_values = set(ESPN_TO_MODEL.values())

print("\n--- Teams in TEAM_RATINGS but missing from API mappings ---")
for team in sorted(TEAM_RATINGS.keys()):
    in_odds = team in odds_model_values
    in_espn = team in espn_model_values
    if not in_odds:
        check(False, f"'{team}' missing from ODDS_API_TO_MODEL values", warn_only=True)
    if not in_espn:
        check(False, f"'{team}' missing from ESPN_TO_MODEL values", warn_only=True)

# Check that ODDS_API_TO_MODEL and ESPN_TO_MODEL produce the same model names
print("\n--- Consistency between ODDS_API_TO_MODEL and ESPN_TO_MODEL ---")
shared_api_keys = set(ODDS_API_TO_MODEL.keys()) & set(ESPN_TO_MODEL.keys())
for key in sorted(shared_api_keys):
    odds_val = ODDS_API_TO_MODEL[key]
    espn_val = ESPN_TO_MODEL[key]
    check(odds_val == espn_val, f"'{key}': Odds API -> '{odds_val}', ESPN -> '{espn_val}'")

# Check keys in one but not other
only_odds = set(ODDS_API_TO_MODEL.keys()) - set(ESPN_TO_MODEL.keys())
only_espn = set(ESPN_TO_MODEL.keys()) - set(ODDS_API_TO_MODEL.keys())
if only_odds:
    for key in sorted(only_odds):
        check(False, f"'{key}' in ODDS_API_TO_MODEL but not ESPN_TO_MODEL", warn_only=True)
if only_espn:
    for key in sorted(only_espn):
        check(False, f"'{key}' in ESPN_TO_MODEL but not ODDS_API_TO_MODEL", warn_only=True)


# ═══════════════════════════════════════════════════════════════════════════════
# 8. PACE DATA COVERAGE
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("8. PACE DATA COVERAGE")
print("=" * 70)

teams_with_pace = set(TEAM_PACE.keys())
teams_without_pace = set(TEAM_RATINGS.keys()) - teams_with_pace
if teams_without_pace:
    print(f"  Teams using default pace (68.0): {sorted(teams_without_pace)}")
# Check all TEAM_PACE keys exist in TEAM_RATINGS
for team in TEAM_PACE:
    check(team in TEAM_RATINGS, f"TEAM_PACE has '{team}' not in TEAM_RATINGS")


# ═══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("STRESS TEST SUMMARY")
print("=" * 70)
print(f"  PASSED: {PASS}")
print(f"  FAILED: {FAIL}")
print(f"  WARNINGS: {WARN}")
print(f"  TOTAL: {PASS + FAIL + WARN}")

if FAIL > 0:
    print(f"\n  *** {FAIL} FAILURES DETECTED - FIX REQUIRED ***")
    sys.exit(1)
elif WARN > 0:
    print(f"\n  All critical tests passed. {WARN} warnings to review.")
else:
    print("\n  ALL TESTS PASSED!")

sys.exit(0)
