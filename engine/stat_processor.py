"""
Multi-dimensional player scoring engine.

Takes raw per-player stats (from nfl_stats.py) and computes 7 component scores:
    volume, efficiency, td_role, team_env, consistency, ceiling, risk

Each component is a 0.0-1.0 percentile rank within position group.
The final base_value is a weighted sum scaled to projected-points range.

Scoring format (PPR/Half/Standard) adjusts component weights.
"""

import bisect
import math

# -- Position-specific weights -----------------------------------------------

_WEIGHTS = {
    "QB": {"volume": 0.15, "efficiency": 0.25, "td_role": 0.20,
           "team_env": 0.05, "consistency": 0.12, "ceiling": 0.13, "risk": 0.10},
    "RB": {"volume": 0.30, "efficiency": 0.15, "td_role": 0.20,
           "team_env": 0.08, "consistency": 0.10, "ceiling": 0.10, "risk": 0.07},
    "WR": {"volume": 0.25, "efficiency": 0.18, "td_role": 0.13,
           "team_env": 0.10, "consistency": 0.10, "ceiling": 0.15, "risk": 0.09},
    "TE": {"volume": 0.22, "efficiency": 0.15, "td_role": 0.20,
           "team_env": 0.13, "consistency": 0.12, "ceiling": 0.10, "risk": 0.08},
}

_FORMAT_DELTAS = {
    "PPR":      {"volume": 0.05, "efficiency": -0.03, "td_role": -0.02},
    "HALF":     {"volume": 0.02, "efficiency": -0.01, "td_role": -0.01},
    "STANDARD": {},
}

# Scale factors: base_value mapped to this range per position
_SCALE = {
    "QB": (180, 430), "RB": (80, 330), "WR": (70, 325), "TE": (60, 230),
}

# Weekly PPR thresholds for boom/bust/startable classification
_STARTER_THRESHOLD = {"QB": 16.0, "RB": 10.0, "WR": 10.0, "TE": 8.0}
_BOOM_THRESHOLD    = {"QB": 25.0, "RB": 18.0, "WR": 18.0, "TE": 14.0}
_BUST_THRESHOLD    = {"QB": 10.0, "RB": 5.0,  "WR": 5.0,  "TE": 4.0}

_COMPONENT_KEYS = ("volume", "efficiency", "td_role", "team_env",
                    "consistency", "ceiling", "risk")


# -- Public API --------------------------------------------------------------

def process_all_players(raw_stats, scoring_format="PPR"):
    """Process all players from raw_stats dict.

    Returns {player_name: {base_value, volume_score, efficiency_score, ...}}
    """
    by_pos = {}
    for name, stats in raw_stats.items():
        pos = stats.get("position", "")
        if pos in _WEIGHTS:
            by_pos.setdefault(pos, {})[name] = stats

    result = {}
    for pos, players in by_pos.items():
        result.update(_score_position_group(players, pos, scoring_format))

    return result


# -- Position group scoring --------------------------------------------------

def _score_position_group(players, position, scoring_format):
    """Score all players at a position using percentile ranking."""
    # Step 1: Compute raw component values
    raw_components = {
        name: _compute_raw_components(stats, position)
        for name, stats in players.items()
    }

    # Step 2: Percentile rank all components at once
    percentiles = _percentile_rank_all_components(raw_components)

    # Step 3: Weighted sum -> base_value
    weights = _get_weights(position, scoring_format)
    lo, hi = _SCALE[position]
    scale_range = hi - lo

    result = {}
    for name, comps in percentiles.items():
        score = (
            comps["volume"]      * weights["volume"]
            + comps["efficiency"]  * weights["efficiency"]
            + comps["td_role"]     * weights["td_role"]
            + comps["team_env"]    * weights["team_env"]
            + comps["consistency"] * weights["consistency"]
            + comps["ceiling"]     * weights["ceiling"]
            - comps["risk"]        * weights["risk"]
        )
        score = max(0.0, min(1.0, score))
        consistency_01 = round(comps["consistency"], 3)

        result[name] = {
            "base_value":        round(lo + score * scale_range, 1),
            "composite_score":   round(score, 4),
            "volume_score":      round(comps["volume"], 3),
            "efficiency_score":  round(comps["efficiency"], 3),
            "td_score":          round(comps["td_role"], 3),
            "team_env_score":    round(comps["team_env"], 3),
            "consistency_score": consistency_01,
            "ceiling_score":     round(comps["ceiling"], 3),
            "risk_score":        round(comps["risk"], 3),
            "consistency":       consistency_01,
            "games_played":      players[name].get("games_played", 0),
            "ppr_pg":            players[name].get("ppr_pg", 0),
        }

    return result


# -- Raw component computation -----------------------------------------------

_RAW_DISPATCH = {}  # populated after function definitions


def _compute_raw_components(stats, position):
    """Compute raw (un-percentiled) component values for a player."""
    weekly = stats.get("weekly_ppr", [])
    base = _RAW_DISPATCH.get(position, _raw_wr)(stats)
    base["consistency"] = _raw_consistency(weekly, position)
    base["ceiling"]     = _raw_ceiling(weekly, position)
    base["risk"]        = _raw_risk(stats, weekly)
    return base


def _raw_qb(s):
    return {
        "volume": (s.get("pass_att_pg", 0) * 1.5
                    + s.get("carries_pg", 0) * 5.0
                    + s.get("rush_yards_pg", 0) * 0.3),
        "efficiency": (s.get("yards_per_att", 0) * 8.0
                       + s.get("td_rate", 0) * 200.0
                       - s.get("int_rate", 0) * 150.0
                       + s.get("passing_epa", 0) * 3.0),
        "td_role": (s.get("pass_tds_pg", 0) * 15.0
                    + s.get("rush_tds_pg", 0) * 20.0
                    + s.get("carries_pg", 0) * 2.0),
        "team_env": (s.get("team_off_ppg", 0) * 0.3
                     + s.get("team_pass_ypg", 0) * 0.02),
    }


def _raw_rb(s):
    touches_pg = s.get("carries_pg", 0) + s.get("receptions_pg", 0)
    return {
        "volume": (touches_pg * 3.0
                   + s.get("targets_pg", 0) * 2.0
                   + s.get("snap_pct", 0) * 0.3),
        "efficiency": (s.get("ypc", 0) * 10.0
                       + s.get("rec_yac_pg", 0) * 1.0
                       + s.get("rushing_epa", 0) * 5.0
                       + s.get("receiving_epa", 0) * 3.0),
        "td_role": (s.get("rush_tds_pg", 0) * 20.0
                    + s.get("rec_tds_pg", 0) * 15.0
                    + touches_pg * 1.5),
        "team_env": (s.get("team_off_ppg", 0) * 0.3
                     + s.get("team_rush_ypg", 0) * 0.03),
    }


def _raw_wr(s):
    return {
        "volume": (s.get("targets_pg", 0) * 5.0
                   + s.get("target_share", 0) * 80.0
                   + s.get("wopr", 0) * 30.0
                   + s.get("snap_pct", 0) * 0.15),
        "efficiency": (s.get("yards_per_target", 0) * 5.0
                       + s.get("catch_rate", 0) * 30.0
                       + s.get("rec_yac_pg", 0) * 0.8
                       + s.get("receiving_epa", 0) * 5.0),
        "td_role": (s.get("rec_tds_pg", 0) * 25.0
                    + s.get("rec_air_yards_pg", 0) * 0.3
                    + s.get("target_share", 0) * 20.0),
        "team_env": (s.get("team_off_ppg", 0) * 0.3
                     + s.get("team_pass_ypg", 0) * 0.03),
    }


def _raw_te(s):
    return {
        "volume": (s.get("targets_pg", 0) * 6.0
                   + s.get("target_share", 0) * 100.0
                   + s.get("snap_pct", 0) * 0.20),
        "efficiency": (s.get("yards_per_target", 0) * 6.0
                       + s.get("catch_rate", 0) * 25.0
                       + s.get("rec_yac_pg", 0) * 1.0
                       + s.get("receiving_epa", 0) * 5.0),
        "td_role": (s.get("rec_tds_pg", 0) * 30.0
                    + s.get("target_share", 0) * 30.0),
        "team_env": (s.get("team_off_ppg", 0) * 0.3
                     + s.get("team_pass_ypg", 0) * 0.03),
    }


_RAW_DISPATCH.update({"QB": _raw_qb, "RB": _raw_rb, "WR": _raw_wr, "TE": _raw_te})


# -- Derived metrics ---------------------------------------------------------

def _raw_consistency(weekly_ppr, position):
    """Higher = more consistent. Based on startable weeks % and low variance."""
    n = len(weekly_ppr)
    if n < 3:
        return 0.0

    threshold = _STARTER_THRESHOLD.get(position, 10.0)
    startable = 0
    total = 0.0
    sq_total = 0.0

    for pts in weekly_ppr:
        if pts >= threshold:
            startable += 1
        total += pts
        sq_total += pts * pts

    mean = total / n
    if mean <= 0:
        return 0.0

    # Variance from sum of squares (avoids second pass)
    variance = max(0.0, sq_total / n - mean * mean)
    stdev = math.sqrt(variance)
    cv = stdev / mean

    startable_pct = startable / n
    cv_score = max(0.0, 1.0 - cv)
    return startable_pct * 60.0 + cv_score * 40.0


def _raw_ceiling(weekly_ppr, position):
    """Higher = more upside. Based on boom weeks and peak performance."""
    n = len(weekly_ppr)
    if n < 3:
        return 0.0

    boom = _BOOM_THRESHOLD.get(position, 18.0)
    boom_count = sum(1 for pts in weekly_ppr if pts >= boom)
    peak = max(weekly_ppr)

    return (boom_count / n) * 60.0 + min(50.0, peak * 1.2)


def _raw_risk(stats, weekly_ppr):
    """Higher = more risky. Based on games missed, fumbles, bust rate."""
    games = stats.get("games_played", 17)
    injury_component = max(0.0, (17 - games) * 5.0)
    fumble_component = stats.get("fumbles_total", 0) * 3.0

    bust_rate = 0.0
    n = len(weekly_ppr)
    if n >= 3:
        bust_thresh = _BUST_THRESHOLD.get(stats.get("position", "WR"), 5.0)
        bust_rate = sum(1 for pts in weekly_ppr if pts < bust_thresh) / n

    return injury_component + fumble_component + bust_rate * 30.0


# -- Helpers ------------------------------------------------------------------

def _percentile_rank_all_components(raw_components):
    """Percentile rank all 7 components for all players in one pass.

    Uses bisect for O(n log n) per component instead of O(n^2).
    Returns {player_name: {component: percentile}}.
    """
    names = list(raw_components.keys())
    n = len(names)
    if n == 0:
        return {}

    divisor = max(1, n - 1)
    result = {name: {} for name in names}

    for key in _COMPONENT_KEYS:
        values = [raw_components[name][key] for name in names]
        sorted_vals = sorted(values)

        for i, name in enumerate(names):
            # bisect_left gives count of values strictly less
            below = bisect.bisect_left(sorted_vals, values[i])
            result[name][key] = below / divisor if n > 1 else 0.5

    return result


def _get_weights(position, scoring_format):
    """Get component weights for a position + scoring format."""
    base = dict(_WEIGHTS.get(position, _WEIGHTS["WR"]))
    for key, delta in _FORMAT_DELTAS.get(scoring_format.upper(), {}).items():
        if key in base:
            base[key] += delta
    return base
