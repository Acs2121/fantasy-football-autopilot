"""
Fetch and cache NFL stats from nflverse.

Reads the nflverse-data parquet releases directly rather than going through
nfl_data_py. That library pins pandas<2 and, as of 0.3.3, still requests
`player_stats_{year}.parquet` -- an asset nflverse renamed to
`stats_player_week_{year}.parquet`, so every weekly fetch 404s. Reading the
URLs ourselves fixes the break and frees the pandas version.

Converts pandas DataFrames to plain dicts at the boundary -- the rest of the
app stays pandas-free.

Cache files go into data/cache/ and are reused until the user explicitly
triggers a refresh.
"""

import datetime
import json
import logging
import math
import os

from .season import last_completed_season

logger = logging.getLogger(__name__)

_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "cache")
_POSITIONS = {"QB", "RB", "WR", "TE"}

_NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"

def _weekly_url(year):
    return f"{_NFLVERSE}/stats_player/stats_player_week_{year}.parquet"

def _roster_url(year):
    return f"{_NFLVERSE}/rosters/roster_{year}.parquet"

def _snaps_url(year):
    return f"{_NFLVERSE}/snap_counts/snap_counts_{year}.parquet"


# nflverse renamed several columns. Map current names back to the ones this
# module's aggregation code expects, so the maths below stays untouched.
_COLUMN_ALIASES = {
    "team": "recent_team",
    "passing_interceptions": "interceptions",
    "sacks_suffered": "sacks",
}

# Columns we need from weekly data (avoids pulling all 150)
_WEEKLY_COLS = [
    "player_display_name", "player_id", "position", "recent_team", "season_type", "week",
    "carries", "rushing_yards", "rushing_tds", "rushing_fumbles",
    "targets", "receptions", "receiving_yards", "receiving_tds",
    "receiving_yards_after_catch", "receiving_air_yards", "receiving_fumbles",
    "attempts", "completions", "passing_yards", "passing_tds", "interceptions",
    "passing_air_yards", "passing_yards_after_catch", "sacks",
    "fantasy_points", "fantasy_points_ppr",
    "target_share", "air_yards_share", "wopr",
    "rushing_epa", "receiving_epa", "passing_epa",
]

# Keys to sum across weeks for totals
_TOTAL_KEYS = [
    "carries", "rushing_yards", "rushing_tds", "rushing_fumbles",
    "targets", "receptions", "receiving_yards", "receiving_tds",
    "receiving_yards_after_catch", "receiving_air_yards", "receiving_fumbles",
    "attempts", "completions", "passing_yards", "passing_tds", "interceptions",
    "passing_air_yards", "passing_yards_after_catch", "sacks",
    "fantasy_points", "fantasy_points_ppr",
]

# Keys to average across weeks (share/rate metrics)
_AVG_KEYS = ["target_share", "air_yards_share", "wopr", "rushing_epa", "receiving_epa", "passing_epa"]


def fetch_and_cache(year=None):
    """Fetch all NFL stats for *year*, cache to disk, return processed dict.

    Defaults to the most recent completed season -- during draft season that is
    last year, since the upcoming season has no games played yet.

    Returns {player_display_name: {stats dict}} keyed by display name.
    """
    year = int(year or last_completed_season())
    os.makedirs(_CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(_CACHE_DIR, f"player_stats_{year}.json")

    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 100:
        with open(cache_path, "r") as f:
            return json.load(f)

    try:
        stats = _build_player_stats(year)
    except Exception as exc:
        logger.error("Failed to fetch NFL stats for %d: %s", year, exc)
        raise

    with open(cache_path, "w") as f:
        json.dump(stats, f)

    return stats


def force_refresh(year=None):
    """Delete cache and re-fetch."""
    year = int(year or last_completed_season())
    cache_path = os.path.join(_CACHE_DIR, f"player_stats_{year}.json")
    if os.path.exists(cache_path):
        os.remove(cache_path)
    return fetch_and_cache(year)


# -- Internal ----------------------------------------------------------------

def _safe_float(v, default=0.0):
    """Convert value to float, handling NaN and None."""
    if v is None:
        return default
    try:
        f = float(v)
        return default if math.isnan(f) else f
    except (TypeError, ValueError):
        return default


def _read_parquet(url, what):
    """Read one nflverse parquet, normalizing renamed columns."""
    import pandas as pd

    logger.info("Fetching %s from nflverse...", what)
    df = pd.read_parquet(url)
    renames = {old: new for old, new in _COLUMN_ALIASES.items()
               if old in df.columns and new not in df.columns}
    return df.rename(columns=renames) if renames else df


def _build_player_stats(year):
    """Pull data from nflverse and aggregate into per-player stat dicts."""
    # 1. Weekly stats (main source)
    weekly = _read_parquet(_weekly_url(year), f"weekly stats {year}")
    weekly = weekly[(weekly["season_type"] == "REG") & (weekly["position"].isin(_POSITIONS))]

    # Keep only the columns we actually use, when present.
    keep = [c for c in _WEEKLY_COLS if c in weekly.columns]
    missing = [c for c in _WEEKLY_COLS if c not in weekly.columns]
    if missing:
        # Not fatal -- _safe_float defaults absent metrics to 0 -- but a silent
        # schema drift is exactly how stale/wrong numbers creep in, so say so.
        logger.warning("nflverse weekly data is missing columns: %s", ", ".join(missing))
    weekly = weekly[keep]

    # Convert to list of dicts once — avoids slow iterrows()
    weekly_records = weekly.to_dict("records")

    # 2. Snap counts
    snap_lookup = _build_snap_lookup(year)

    # 3. Rosters — use the UPCOMING season's roster so teams, rookie flags and
    #    cross-platform ids reflect where players are now, not last year.
    roster_lookup = _build_roster_lookup(year + 1, fallback_year=year)

    # Group weekly records by player name and compute team totals in one pass
    player_weeks = {}
    team_data = {}  # team -> {ppr_total, pass_total, rush_total, weeks: set}
    for row in weekly_records:
        name = row.get("player_display_name", "")
        if not name:
            continue
        player_weeks.setdefault(name, []).append(row)

        team = row.get("recent_team", "")
        if team:
            td = team_data.setdefault(team, {"ppr": 0.0, "pass": 0.0, "rush": 0.0, "weeks": set()})
            td["ppr"]  += _safe_float(row.get("fantasy_points_ppr"))
            td["pass"] += _safe_float(row.get("passing_yards"))
            td["rush"] += _safe_float(row.get("rushing_yards"))
            td["weeks"].add(row.get("week", 0))

    # Finalize team totals
    team_totals = {}
    for team, td in team_data.items():
        n = max(1, len(td["weeks"]))
        team_totals[team] = {
            "ppg":      round(td["ppr"] / n, 1),
            "pass_ypg": round(td["pass"] / n, 1),
            "rush_ypg": round(td["rush"] / n, 1),
        }

    # Aggregate per player
    result = {}
    for name, weeks in player_weeks.items():
        if len(weeks) < 3:
            continue

        first    = weeks[0]
        position = str(first.get("position", ""))
        team     = str(first.get("recent_team", ""))
        pid      = str(first.get("player_id", ""))

        stats = _aggregate_weekly(weeks)
        stats["games_played"] = len(weeks)
        stats["position"]     = position
        stats["team"]         = team
        stats["player_id"]    = pid

        # Snap %
        snap_vals = snap_lookup.get((name, team))
        stats["snap_pct"] = round(sum(snap_vals) / len(snap_vals), 1) if snap_vals else 0.0

        # Roster info
        rinfo = roster_lookup.get(pid)
        if rinfo:
            stats.update(rinfo)

        # Team environment
        tt = team_totals.get(team, {})
        stats["team_off_ppg"]  = tt.get("ppg", 0)
        stats["team_pass_ypg"] = tt.get("pass_ypg", 0)
        stats["team_rush_ypg"] = tt.get("rush_ypg", 0)

        # Weekly fantasy points for consistency/ceiling
        stats["weekly_ppr"] = [round(_safe_float(w.get("fantasy_points_ppr")), 1) for w in weeks]

        result[name] = stats

    logger.info("Processed stats for %d players", len(result))
    return result


def _build_snap_lookup(year):
    """Build {(player_name, team): [offense_pct values]} from snap counts."""
    try:
        snaps = _read_parquet(_snaps_url(year), f"snap counts {year}")
        snaps = snaps[snaps["game_type"] == "REG"]
    except Exception as exc:
        logger.warning("Snap counts unavailable for %d: %s", year, exc)
        return {}

    lookup = {}
    for row in snaps.to_dict("records"):
        key = (row.get("player", ""), row.get("team", ""))
        lookup.setdefault(key, []).append(_safe_float(row.get("offense_pct", 0)))
    return lookup


def _age_from_birth_date(value, on_date=None):
    """Age in years from a birth date, or 0.0 when unknown."""
    if not value:
        return 0.0
    try:
        if isinstance(value, str):
            born = datetime.date.fromisoformat(value[:10])
        else:
            born = datetime.date(value.year, value.month, value.day)
    except (ValueError, TypeError, AttributeError):
        return 0.0
    today = on_date or datetime.date.today()
    years = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    return float(years) if 15 <= years <= 60 else 0.0


def _build_roster_lookup(year, fallback_year=None):
    """Build {player_id: {age, years_exp, espn_id, sleeper_id, ...}} from rosters.

    nflverse rosters carry cross-platform ids (espn_id, sleeper_id), which is
    what lets ESPN draft sync match on id instead of guessing from names.
    Age isn't a column -- it's derived from birth_date.
    """
    rosters = None
    for attempt in [y for y in (year, fallback_year) if y]:
        try:
            rosters = _read_parquet(_roster_url(attempt), f"rosters {attempt}")
            break
        except Exception as exc:
            logger.warning("Roster data unavailable for %d: %s", attempt, exc)
    if rosters is None:
        return {}

    lookup = {}
    for row in rosters.to_dict("records"):
        # nflverse uses gsis_id here; weekly stats call the same value player_id.
        pid = row.get("player_id") or row.get("gsis_id") or ""
        if not pid:
            continue
        espn_id = row.get("espn_id")
        sleeper_id = row.get("sleeper_id")
        rookie_year = row.get("rookie_year") or row.get("entry_year")
        lookup[str(pid)] = {
            "age":         _age_from_birth_date(row.get("birth_date")),
            "years_exp":   int(_safe_float(row.get("years_exp", 0))),
            "espn_id":     str(int(espn_id)) if _safe_float(espn_id) else "",
            "sleeper_id":  str(int(sleeper_id)) if _safe_float(sleeper_id) else "",
            "rookie_year": int(_safe_float(rookie_year)) if rookie_year else 0,
            "current_team": str(row.get("recent_team") or row.get("team") or ""),
        }
    return lookup


def _aggregate_weekly(weeks):
    """Aggregate weekly row dicts into per-game averages and rates.

    Single pass over weeks for totals and averages.
    """
    g = float(len(weeks))
    totals  = {k: 0.0 for k in _TOTAL_KEYS}
    avg_sums = {k: 0.0 for k in _AVG_KEYS}

    for w in weeks:
        for k in _TOTAL_KEYS:
            totals[k] += _safe_float(w.get(k))
        for k in _AVG_KEYS:
            avg_sums[k] += _safe_float(w.get(k))

    s = {}

    # Per-game averages
    s["carries_pg"]       = round(totals["carries"] / g, 1)
    s["rush_yards_pg"]    = round(totals["rushing_yards"] / g, 1)
    s["rush_tds_pg"]      = round(totals["rushing_tds"] / g, 2)
    s["targets_pg"]       = round(totals["targets"] / g, 1)
    s["receptions_pg"]    = round(totals["receptions"] / g, 1)
    s["rec_yards_pg"]     = round(totals["receiving_yards"] / g, 1)
    s["rec_tds_pg"]       = round(totals["receiving_tds"] / g, 2)
    s["rec_yac_pg"]       = round(totals["receiving_yards_after_catch"] / g, 1)
    s["rec_air_yards_pg"] = round(totals["receiving_air_yards"] / g, 1)
    s["ppr_pg"]           = round(totals["fantasy_points_ppr"] / g, 1)

    # Rushing efficiency
    carries = totals["carries"]
    s["ypc"] = round(totals["rushing_yards"] / carries, 2) if carries > 0 else 0.0

    # Receiving efficiency
    targets = totals["targets"]
    if targets > 0:
        s["catch_rate"]      = round(totals["receptions"] / targets, 3)
        s["yards_per_target"] = round(totals["receiving_yards"] / targets, 2)
    else:
        s["catch_rate"]       = 0.0
        s["yards_per_target"] = 0.0

    # Passing efficiency
    attempts = totals["attempts"]
    if attempts > 0:
        s["pass_att_pg"]   = round(attempts / g, 1)
        s["comp_pct"]      = round(totals["completions"] / attempts, 3)
        s["pass_yards_pg"] = round(totals["passing_yards"] / g, 1)
        s["pass_tds_pg"]   = round(totals["passing_tds"] / g, 2)
        s["int_pg"]        = round(totals["interceptions"] / g, 2)
        s["yards_per_att"] = round(totals["passing_yards"] / attempts, 2)
        s["td_rate"]       = round(totals["passing_tds"] / attempts, 4)
        s["int_rate"]      = round(totals["interceptions"] / attempts, 4)
        s["sacks_pg"]      = round(totals["sacks"] / g, 2)
    else:
        for k in ("pass_att_pg", "comp_pct", "pass_yards_pg", "pass_tds_pg",
                   "int_pg", "yards_per_att", "td_rate", "int_rate", "sacks_pg"):
            s[k] = 0.0

    # Share/rate metrics (pre-summed in single pass)
    s["target_share"]    = round(avg_sums["target_share"] / g, 3)
    s["air_yards_share"] = round(avg_sums["air_yards_share"] / g, 3)
    s["wopr"]            = round(avg_sums["wopr"] / g, 3)
    s["rushing_epa"]     = round(avg_sums["rushing_epa"] / g, 2)
    s["receiving_epa"]   = round(avg_sums["receiving_epa"] / g, 2)
    s["passing_epa"]     = round(avg_sums["passing_epa"] / g, 2)

    # Fumbles and totals
    s["fumbles_total"] = int(totals["rushing_fumbles"] + totals["receiving_fumbles"])
    s["total_tds"]     = int(totals["rushing_tds"] + totals["receiving_tds"] + totals["passing_tds"])
    s["total_ppr"]     = round(totals["fantasy_points_ppr"], 1)

    return s
