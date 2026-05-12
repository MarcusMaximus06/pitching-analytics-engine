import requests


def blend_pitcher_form(season_fip, recent_era, recent_weight=0.35):
    return (season_fip * (1 - recent_weight)) + (recent_era * recent_weight)


def fetch_pitcher_recent_era(pitcher_name):
    """
    Attempts to estimate recent pitcher form.
    Current safe version returns None unless future ID mapping is added.
    """

    if not pitcher_name or pitcher_name == "League Average SP" or pitcher_name == "Unknown":
        return None

    return None
