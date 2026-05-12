import requests


def blend_pitcher_form(season_fip, recent_era, recent_weight=0.35):
    return (season_fip * (1 - recent_weight)) + (recent_era * recent_weight)


def fetch_pitcher_recent_era(pitcher_id, max_games=3):
    if not pitcher_id:
        return None

    url = (
        f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats"
        f"?stats=gameLog&group=pitching&season=2026"
    )

    try:
        response = requests.get(url, timeout=15).json()
        splits = response.get("stats", [{}])[0].get("splits", [])

        recent_games = []

        for game in reversed(splits):
            stat = game.get("stat", {})

            innings = float(stat.get("inningsPitched", 0) or 0)
            earned_runs = float(stat.get("earnedRuns", 0) or 0)

            if innings > 0:
                recent_games.append({
                    "innings": innings,
                    "earned_runs": earned_runs
                })

            if len(recent_games) >= max_games:
                break

        if not recent_games:
            return None

        total_ip = sum(g["innings"] for g in recent_games)
        total_er = sum(g["earned_runs"] for g in recent_games)

        if total_ip <= 0:
            return None

        recent_era = (total_er * 9) / total_ip

        return recent_era

    except Exception:
        return None
