"""
Fetch and cache NFL stats from nfl_data_py (nflverse).

Pulls weekly player stats, snap counts, and roster data for a given season.
Converts pandas DataFrames to plain dicts at the boundary -- the rest of the
app stays pandas-free.

Cache files go into data/cache/ and are reused until the user explicitly
triggers a refresh.
"""

import json
import logging
import math
import os

logger = logging.getLogger(__name__)

_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "cache")
_POSITIONS = {"QB", "RB", "WR", "TE"}

# Columns we need from weekly data (avoids pulling all 53)
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


def fetch_and_cache(year=2024):
    """Fetch all NFL stats for *year*, cache to disk, return processed dict.

    Returns {player_display_name: {stats dict}} keyed by display name.
    """
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


def force_refresh(year=2024):
    """Delete cache and re-fetch."""
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


def _build_player_stats(year):
    """Pull data from nfl_data_py and aggregate into per-player stat dicts."""
    import nfl_data_py as nfl

    # 1. Weekly stats (main source)
    logger.info("Fetching weekly data for %d...", year)
    weekly = nfl.import_weekly_data([year], downcast=False)
    weekly = weekly[(weekly["season_type"] == "REG") & (weekly["position"].isin(_POSITIONS))]

    # Convert to list of dicts once — avoids slow iterrows()
    weekly_records = weekly.to_dict("records")

    # 2. Snap counts
    snap_lookup = _build_snap_lookup(nfl, year)

    # 3. Rosters
    roster_lookup = _build_roster_lookup(nfl, year)

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


def _build_snap_lookup(nfl, year):
    """Build {(player_name, team): [offense_pct values]} from snap counts."""
    try:
        snaps = nfl.import_snap_counts([year])
        snaps = snaps[snaps["game_type"] == "REG"]
    except Exception:
        logger.warning("Snap counts unavailable for %d", year)
        return {}

    lookup = {}
    for row in snaps.to_dict("records"):
        key = (row.get("player", ""), row.get("team", ""))
        lookup.setdefault(key, []).append(_safe_float(row.get("offense_pct", 0)))
    return lookup


def _build_roster_lookup(nfl, year):
    """Build {player_id: {age, years_exp, espn_id}} from seasonal rosters."""
    try:
        rosters = nfl.import_seasonal_rosters([year])
    except Exception:
        logger.warning("Roster data unavailable for %d", year)
        return {}

    lookup = {}
    for row in rosters.to_dict("records"):
        pid = row.get("player_id", "")
        if pid:
            espn_id = row.get("espn_id")
            lookup[pid] = {
                "age":       _safe_float(row.get("age", 0)),
                "years_exp": int(_safe_float(row.get("years_exp", 0))),
                "espn_id":   str(espn_id) if espn_id else "",
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
