import requests


def blend_pitcher_form(season_fip, recent_era, recent_weight=0.35):
    """
    Blend season-long pitcher skill with recent performance.
    Lower is better.
    """

    return (
        (season_fip * (1 - recent_weight)) +
        (recent_era * recent_weight)
    )


def fetch_pitcher_recent_era(pitcher_name):
    """
    Placeholder for future pitcher recent-form API integration.
    """

    return None
