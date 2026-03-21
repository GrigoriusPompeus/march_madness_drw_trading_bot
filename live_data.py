"""
Live NCAA Tournament Data Fetcher
Fetches real-time scores and bracket updates from ESPN API
to keep the model's fair values updated as games complete.
"""

import asyncio
import aiohttp
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

log = logging.getLogger("live_data")

ESPN_SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/"
    "mens-college-basketball/scoreboard"
)


async def fetch_tournament_scores(
    session: aiohttp.ClientSession,
    date: Optional[str] = None,
) -> List[Dict]:
    """
    Fetch NCAA tournament scores from ESPN API.
    Returns list of game dicts with: home_team, away_team, home_score,
    away_score, status (scheduled/in_progress/final), clock, period.
    """
    if date is None:
        date = datetime.now().strftime("%Y%m%d")

    params = {
        "dates": date,
        "groups": "100",  # NCAA tournament group
        "limit": "100",
    }

    games = []
    try:
        async with session.get(ESPN_SCOREBOARD, params=params) as resp:
            if resp.status != 200:
                log.warning(f"ESPN API returned status {resp.status}")
                return games
            data = await resp.json()

        for event in data.get("events", []):
            competition = event.get("competitions", [{}])[0]
            competitors = competition.get("competitors", [])

            if len(competitors) != 2:
                continue

            game = {"id": event.get("id")}

            for comp in competitors:
                team_data = comp.get("team", {})
                name = team_data.get("displayName", "Unknown")
                score = int(comp.get("score", 0))
                seed_str = comp.get("curatedRank", {}).get("current", 0)

                if comp.get("homeAway") == "home":
                    game["home_team"] = name
                    game["home_score"] = score
                    game["home_seed"] = seed_str
                else:
                    game["away_team"] = name
                    game["away_score"] = score
                    game["away_seed"] = seed_str

            status = competition.get("status", {})
            status_type = status.get("type", {})
            game["status"] = status_type.get("name", "STATUS_SCHEDULED")
            game["clock"] = status.get("displayClock", "")
            game["period"] = status.get("period", 0)

            games.append(game)

    except Exception as e:
        log.error(f"Error fetching ESPN scores: {e}")

    return games


_elim_cache: Tuple[Set[str], Dict[str, str]] = (set(), {})
_elim_cache_time: float = 0.0
_ELIM_CACHE_TTL: float = 300.0  # Only re-fetch all 6 days every 5 minutes

async def get_eliminated_teams(
    session: aiohttp.ClientSession,
) -> Tuple[Set[str], Dict[str, str]]:
    """
    Check all tournament dates to find eliminated teams.
    Caches results for 5 minutes to avoid excessive ESPN API calls.
    Returns:
        eliminated: set of eliminated team names
        game_results: dict of winner -> loser for completed games
    """
    global _elim_cache, _elim_cache_time
    import time as _time
    now = _time.time()
    if _elim_cache[0] and (now - _elim_cache_time) < _ELIM_CACHE_TTL:
        # Only check today for new results (1 API call instead of 6)
        today = datetime.now().strftime("%Y%m%d")
        games = await fetch_tournament_scores(session, today)
        eliminated, results = set(_elim_cache[0]), dict(_elim_cache[1])
        new_found = False
        for game in games:
            if game.get("status") == "STATUS_FINAL":
                home = game.get("home_team", "")
                away = game.get("away_team", "")
                h_score = game.get("home_score", 0)
                a_score = game.get("away_score", 0)
                if h_score > a_score:
                    winner, loser = home, away
                else:
                    winner, loser = away, home
                if loser not in eliminated:
                    new_found = True
                    log.info(f"Game result: {winner} def. {loser} ({h_score}-{a_score})")
                eliminated.add(loser)
                results[winner] = loser
        if new_found:
            _elim_cache = (eliminated, results)
        return eliminated, results

    # Full refresh: check all days
    eliminated = set()
    results = {}

    today = datetime.now()
    for delta in range(-5, 1):
        date = (today + timedelta(days=delta)).strftime("%Y%m%d")
        games = await fetch_tournament_scores(session, date)

        for game in games:
            if game.get("status") == "STATUS_FINAL":
                home = game.get("home_team", "")
                away = game.get("away_team", "")
                h_score = game.get("home_score", 0)
                a_score = game.get("away_score", 0)

                if h_score > a_score:
                    winner, loser = home, away
                else:
                    winner, loser = away, home

                eliminated.add(loser)
                results[winner] = loser
                log.info(f"Game result: {winner} def. {loser} ({h_score}-{a_score})")

    _elim_cache = (eliminated, results)
    _elim_cache_time = now
    return eliminated, results


async def get_live_games(
    session: aiohttp.ClientSession,
) -> List[Dict]:
    """Get currently live (in-progress) tournament games."""
    today = datetime.now().strftime("%Y%m%d")
    games = await fetch_tournament_scores(session, today)
    return [g for g in games if g.get("status") in ("STATUS_IN_PROGRESS", "STATUS_HALFTIME", "STATUS_END_PERIOD")]


# ESPN team name -> our model team name mapping
ESPN_TO_MODEL = {
    "Duke Blue Devils": "Duke",
    "Arizona Wildcats": "Arizona",
    "Michigan Wolverines": "Michigan",
    "Florida Gators": "Florida",
    "Houston Cougars": "Houston",
    "Iowa State Cyclones": "Iowa State",
    "Illinois Fighting Illini": "Illinois",
    "Purdue Boilermakers": "Purdue",
    "Michigan State Spartans": "Michigan State",
    "Gonzaga Bulldogs": "Gonzaga",
    "UConn Huskies": "Connecticut",
    "Connecticut Huskies": "Connecticut",
    "Vanderbilt Commodores": "Vanderbilt",
    "Virginia Cavaliers": "Virginia",
    "Nebraska Cornhuskers": "Nebraska",
    "Kansas Jayhawks": "Kansas",
    "Tennessee Volunteers": "Tennessee",
    "Alabama Crimson Tide": "Alabama",
    "Arkansas Razorbacks": "Arkansas",
    "Louisville Cardinals": "Louisville",
    "Texas Tech Red Raiders": "Texas Tech",
    "St. John's Red Storm": "St. John's",
    "North Carolina Tar Heels": "North Carolina",
    "Wisconsin Badgers": "Wisconsin",
    "Saint Mary's Gaels": "Saint Mary's",
    "UCLA Bruins": "UCLA",
    "BYU Cougars": "BYU",
    "Brigham Young Cougars": "BYU",
    "Clemson Tigers": "Clemson",
    "Kentucky Wildcats": "Kentucky",
    "Villanova Wildcats": "Villanova",
    "Ohio State Buckeyes": "Ohio State",
    "Georgia Bulldogs": "Georgia",
    "Miami Hurricanes": "Miami FL",
    "Iowa Hawkeyes": "Iowa",
    "Missouri Tigers": "Missouri",
    "TCU Horned Frogs": "TCU",
    "Texas A&M Aggies": "Texas A&M",
    "UCF Knights": "UCF",
    "VCU Rams": "VCU",
    "Santa Clara Broncos": "Santa Clara",
    "Utah State Aggies": "Utah State",
    "South Florida Bulls": "South Florida",
    "Saint Louis Billikens": "Saint Louis",
    "SMU Mustangs": "SMU",
    "NC State Wolfpack": "NC State",
    "Texas Longhorns": "Texas",
    "Northern Iowa Panthers": "Northern Iowa",
    "Akron Zips": "Akron",
    "McNeese Cowboys": "McNeese",
    "High Point Panthers": "High Point",
    "Hofstra Pride": "Hofstra",
    "Troy Trojans": "Troy",
    "Hawaii Rainbow Warriors": "Hawaii",
    "Hawai'i Rainbow Warriors": "Hawaii",
    "Miami (OH) RedHawks": "Miami OH",
    "Penn Quakers": "Penn",
    "Pennsylvania Quakers": "Penn",
    "Kennesaw State Owls": "Kennesaw State",
    "North Dakota State Bison": "North Dakota State",
    "Cal Baptist Lancers": "Cal Baptist",
    "California Baptist Lancers": "Cal Baptist",
    "Miami OH RedHawks": "Miami OH",
    "Wright State Raiders": "Wright State",
    "Furman Paladins": "Furman",
    "Queens Royals": "Queens",
    "Idaho Vandals": "Idaho",
    "Tennessee State Tigers": "Tennessee State",
    "Siena Saints": "Siena",
    "LIU Sharks": "Long Island",
    "Long Island University Sharks": "Long Island",
    "Lehigh Mountain Hawks": "Lehigh",
    "Prairie View A&M Panthers": "Prairie View A&M",
    "Howard Bison": "Howard",
    "UMBC Retrievers": "UMBC",
}


def espn_to_model_name(espn_name: str) -> Optional[str]:
    """Convert ESPN team name to our model name."""
    # Exact match
    if espn_name in ESPN_TO_MODEL:
        return ESPN_TO_MODEL[espn_name]

    # Partial match - only check if a known key appears IN the input (not vice versa)
    # to avoid "Michigan" matching "Michigan State Spartans".
    # If the input is shorter than all keys, step 3 (TEAM_RATINGS regex) handles it.
    import re
    for espn, model in sorted(ESPN_TO_MODEL.items(), key=lambda x: len(x[0]), reverse=True):
        if re.search(r'\b' + re.escape(espn) + r'\b', espn_name):
            return model

    # Try matching against model names - sort by length descending so
    # "Michigan State" is checked before "Michigan", etc.
    import re
    from model import TEAM_RATINGS
    for team in sorted(TEAM_RATINGS.keys(), key=len, reverse=True):
        if re.search(r'\b' + re.escape(team.lower()) + r'\b', espn_name.lower()):
            return team

    log.warning(f"Could not map ESPN name: {espn_name}")
    return None


if __name__ == "__main__":
    """Test: fetch and display current tournament scores."""
    async def test():
        async with aiohttp.ClientSession() as session:
            print("Fetching tournament scores...")
            games = await fetch_tournament_scores(session)
            if not games:
                print("No tournament games found for today.")
                # Try yesterday and tomorrow
                for delta in [-1, 1]:
                    date = (datetime.now() + timedelta(days=delta)).strftime("%Y%m%d")
                    games = await fetch_tournament_scores(session, date)
                    if games:
                        print(f"Found {len(games)} games for {date}:")
                        break

            for game in games:
                status = game.get("status", "")
                home = game.get("home_team", "?")
                away = game.get("away_team", "?")
                hs = game.get("home_score", 0)
                as_ = game.get("away_score", 0)
                print(f"  [{status}] {away} {as_} - {home} {hs}")

            print("\nChecking for eliminated teams...")
            eliminated, results = await get_eliminated_teams(session)
            if eliminated:
                print(f"Eliminated teams: {eliminated}")
            else:
                print("No teams eliminated yet.")

    asyncio.run(test())
