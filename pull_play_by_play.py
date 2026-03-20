import requests
import csv
from datetime import datetime, timedelta
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

SCOREBOARD_URL = "http://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
SUMMARY_URL = "http://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/summary"

def get_game_ids(date_str):
    url = f"{SCOREBOARD_URL}?dates={date_str}&limit=200&groups=100"
    try:
        resp = requests.get(url)
        resp.raise_for_status()
        data = resp.json()
        games = []
        for event in data.get("events", []):
            game_id = event["id"]
            
            # Extract basic team names just in case
            comp = event.get("competitions", [{}])[0]
            competitors = comp.get("competitors", [])
            home = away = "Unknown"
            for c in competitors:
                if c.get("homeAway") == "home":
                    home = c.get("team", {}).get("displayName", "Unknown Home")
                else:
                    away = c.get("team", {}).get("displayName", "Unknown Away")
                    
            games.append({
                "id": game_id,
                "home": home,
                "away": away
            })
        return games
    except Exception as e:
        logging.error(f"Failed to get games for {date_str}: {e}")
        return []

def get_play_by_play(game_id, home_team, away_team):
    url = f"{SUMMARY_URL}?event={game_id}"
    plays_data = []
    try:
        resp = requests.get(url)
        resp.raise_for_status()
        data = resp.json()
        
        # Sometime teams are formatted slightly differently in summary, use the ones passed from scoreboard
        plays = data.get("plays", [])
        for play in plays:
            wallclock = play.get("wallclock")
            # If no wallclock is present, we try to estimate or skip
            if not wallclock:
                continue
                
            home_score = play.get("homeScore", 0)
            away_score = play.get("awayScore", 0)
            period = play.get("period", {}).get("number", 0)
            clock = play.get("clock", {}).get("displayValue", "")
            desc = play.get("text", "")
            
            plays_data.append({
                "game_id": game_id,
                "wallclock_utc": wallclock,
                "home_team": home_team,
                "away_team": away_team,
                "home_score": home_score,
                "away_score": away_score,
                "period": period,
                "game_clock": clock,
                "description": desc
            })
        return plays_data
    except Exception as e:
        logging.error(f"Failed to get plays for game {game_id}: {e}")
        return []

def main():
    # Dates for 2024 NCAA Men's Tournament (First round through Championship)
    target_dates = [
        "20240321", "20240322", "20240323", "20240324",
        "20240328", "20240329", "20240330", "20240331",
        "20240406", "20240408"
    ]
    all_plays = []
    
    for d in target_dates:
        logging.info(f"Fetching games for {d}...")
        games = get_game_ids(d)
        logging.info(f"Found {len(games)} games.")
        
        for g in games:
            logging.info(f"Fetching play-by-play for {g['home']} vs {g['away']} ({g['id']})")
            pbp = get_play_by_play(g['id'], g['home'], g['away'])
            all_plays.extend(pbp)
            time.sleep(0.1) # Be nice to the API
            
    if not all_plays:
        logging.warning("No plays fetched!")
        return

    # Write out data to CSV
    output_file = "game_scores.csv"
    logging.info(f"Writing {len(all_plays)} rows to {output_file}...")
    keys = all_plays[0].keys()
    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(all_plays)
    logging.info("Done!")

if __name__ == "__main__":
    main()
