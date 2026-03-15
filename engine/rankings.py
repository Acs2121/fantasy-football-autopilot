"""
Player rankings via Value Based Drafting (VBD).

VBD = player_value - positional_baseline
where baseline = last startable player at each position league-wide.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

_POS_CONSISTENCY_DEFAULTS = {
    "QB": 0.80, "RB": 0.50, "WR": 0.40, "TE": 0.45, "K": 0.70, "DST": 0.30,
}


def load_players(filepath=None):
    """Load player data from JSON, applying consistency defaults."""
    if filepath is None:
        filepath = os.path.join(os.path.dirname(__file__), "..", "data", "players_2025.json")
    try:
        with open(filepath, "r") as f:
            players = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.error("Failed to load player data from %s: %s", filepath, exc)
        return []

    for p in players:
        p.setdefault("consistency", _POS_CONSISTENCY_DEFAULTS.get(p["position"], 0.5))
    return players


def compute_vbd(players, roster_slots, num_teams):
    """Compute VBD and sleeper scores for all players in-place. Returns players."""
    if not players:
        return players

    # Group by position once — reused by both VBD and sleeper scoring
    by_position = {}
    for p in players:
        by_position.setdefault(p["position"], []).append(p)

    _assign_vbd_scores(players, by_position, roster_slots, num_teams)
    _assign_sleeper_scores(by_position)
    return players


def _starter_counts(roster_slots):
    """How many starters of each position are drafted across the whole league.

    FLEX expands demand for RB/WR/TE — each gets +FLEX to reflect
    the wider pool competing for roster spots.
    """
    flex = roster_slots.get("FLEX", 1)
    return {
        "QB":  roster_slots.get("QB", 1),
        "RB":  roster_slots.get("RB", 2) + flex,
        "WR":  roster_slots.get("WR", 2) + flex,
        "TE":  roster_slots.get("TE", 1) + flex,
        "K":   roster_slots.get("K", 1),
        "DST": roster_slots.get("DST", 1),
    }


def _player_value(p):
    """Return the best available value metric: base_value > projected_points."""
    return p.get("base_value", p.get("projected_points", 0))


def _assign_vbd_scores(players, by_position, roster_slots, num_teams):
    """Assign vbd_score to each player in-place."""
    counts = _starter_counts(roster_slots)

    # Sort each position group by value (descending) and find baselines
    baselines = {}
    for pos, count in counts.items():
        pos_players = by_position.get(pos, [])
        if not pos_players:
            baselines[pos] = 0
            continue
        pos_players.sort(key=_player_value, reverse=True)
        baseline_idx = min(count * num_teams - 1, len(pos_players) - 1)
        baselines[pos] = _player_value(pos_players[baseline_idx])

    for p in players:
        p["vbd_score"] = round(_player_value(p) - baselines.get(p["position"], 0), 1)


def _assign_sleeper_scores(by_position):
    """Assign sleeper_score to each player in-place.

    Sleeper score = how much later a player is drafted (ADP) relative to
    how good they actually are (VBD rank within position).
    Only players with positive VBD qualify. Range: 0.0 – 1.0.
    """
    for pos_players in by_position.values():
        n = len(pos_players)
        if n < 2:
            for p in pos_players:
                p["sleeper_score"] = 0.0
            continue

        # VBD sort is already done by _assign_vbd_scores, but re-sorting
        # by ADP is unavoidable since it's a different key
        adp_sorted = sorted(pos_players, key=lambda x: x.get("adp", 999))
        adp_rank = {p["id"]: i + 1 for i, p in enumerate(adp_sorted)}

        vbd_sorted = sorted(pos_players, key=lambda x: x.get("vbd_score", 0), reverse=True)
        vbd_rank = {p["id"]: i + 1 for i, p in enumerate(vbd_sorted)}

        for p in pos_players:
            diff = adp_rank[p["id"]] - vbd_rank[p["id"]]
            if diff > 0 and p.get("vbd_score", 0) > 0:
                p["sleeper_score"] = round(min(1.0, diff / n), 3)
            else:
                p["sleeper_score"] = 0.0
