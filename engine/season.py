"""
Season arithmetic, in one place.

Every module that used to hardcode a year (2024, 2025) now asks here instead,
so the app stops needing an annual edit to stay correct.
"""

import datetime


def current_season_year(today=None):
    """Return the NFL season year currently in play.

    The season spans September through February, and fantasy platforms roll
    over to the new seasonId during the summer as drafts begin. June onward
    belongs to the season named for the current calendar year; January through
    May still belongs to the prior season.
    """
    today = today or datetime.date.today()
    return today.year if today.month >= 6 else today.year - 1


def last_completed_season(today=None):
    """Return the most recent season with a full set of played games.

    Historical stats (what players actually did) can only come from a finished
    season. During the summer -- draft season -- that is the prior year, not
    the season about to start.
    """
    today = today or datetime.date.today()
    season = current_season_year(today)
    # A season is only "complete" once its playoffs are done, in February of
    # the following calendar year. Before then, the prior season is the
    # newest one with a full slate.
    if today.month >= 6 or (today.year == season and today.month < 3):
        return season - 1 if today.month >= 6 else season
    return season
