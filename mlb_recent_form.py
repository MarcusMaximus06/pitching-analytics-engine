import pandas as pd


def blend_recent_and_season(season_value, recent_value, recent_weight=0.30):
    """
    Blend season-long and recent-form values.
    """

    return (
        (season_value * (1 - recent_weight)) +
        (recent_value * recent_weight)
    )


def calculate_recent_form_adjustment(
    season_runs_scored,
    recent_runs_scored,
    season_runs_allowed,
    recent_runs_allowed,
    recent_weight=0.30
):
    """
    Returns blended offensive and defensive values.
    """

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
