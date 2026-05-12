import requests
from datetime import datetime, timedelta


def blend_recent_and_season(season_value, recent_value, recent_weight=0.30):
    return (season_value * (1 - recent_weight)) + (recent_value * recent_weight)


def calculate_recent_form_adjustment(
    season_runs_scored,
    recent_runs_scored,
    season_runs_allowed,
    recent_runs_allowed,
    recent_weight=0.30
):
    blended_offense = blend_recent_and_season(
        season_runs_scored,
        recent_runs_scored,
        recent_weight
    )

    blended_defense = blend_recent_and_season(
        season_runs_allowed,
        recent_runs_allowed,
        recent_weight
    )

    return {
        "offense": blended_offense,
        "defense": blended_defense
    }


def fetch_recent_mlb_team_form(team_name, days_back=14):
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days_back)

    url = (
        "https://statsapi.mlb.com/api/v1/schedule"
        f"?sportId=1"
        f"&startDate={start_date.strftime('%Y-%m-%d')}"
        f"&endDate={end_date.strftime('%Y-%m-%d')}"
    )

    try:
        response = requests.get(url, timeout=15).json()
        runs_scored = []
        runs_allowed = []

        for date_block in response.get("dates", []):
            for game in date_block.get("games", []):
                if game.get("status", {}).get("abstractGameState") != "Final":
                    continue

                away_team = game["teams"]["away"]["team"]["name"]
                home_team = game["teams"]["home"]["team"]["name"]
                away_score = game["teams"]["away"].get("score")
                home_score = game["teams"]["home"].get("score")

                if away_score is None or home_score is None:
                    continue

                if team_name == away_team:
                    runs_scored.append(away_score)
                    runs_allowed.append(home_score)

                elif team_name == home_team:
                    runs_scored.append(home_score)
                    runs_allowed.append(away_score)

        if not runs_scored:
            return None

        return {
            "recent_rs_per_g": sum(runs_scored) / len(runs_scored),
            "recent_ra_per_g": sum(runs_allowed) / len(runs_allowed),
            "recent_games": len(runs_scored)
        }

    except Exception:
        return None
