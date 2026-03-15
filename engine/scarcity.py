_SCORED_POSITIONS = ["QB", "RB", "WR", "TE", "K", "DST"]
_FLEX_POSITIONS   = {"RB", "WR", "TE"}
_ALERT_POSITIONS  = {"QB", "RB", "WR", "TE"}
_ELITE_TIER_MAX   = 4
_ELITE_ALERT_THRESHOLD   = 3
_SCARCITY_ALERT_THRESHOLD = 0.7


def compute_scarcity(draft_state):
    """Calculate positional scarcity metrics for all scored positions."""
    num_teams = draft_state.num_teams
    roster_slots = draft_state.roster_slots
    available_ids = draft_state.available_ids

    # Pre-group all players and available players by position once
    by_pos_all   = _group_by_position(draft_state.all_players)
    by_pos_avail = _group_by_position(draft_state.get_available_players())

    starter_demand = _compute_starter_demand(roster_slots, num_teams)

    return {
        pos: _position_scarcity(
            pos,
            by_pos_all.get(pos, []),
            by_pos_avail.get(pos, []),
            available_ids,
            starter_demand[pos],
        )
        for pos in _SCORED_POSITIONS
    }


def _group_by_position(players):
    """Return a dict mapping position -> list of players."""
    groups = {}
    for p in players:
        groups.setdefault(p["position"], []).append(p)
    return groups


def _compute_starter_demand(roster_slots, num_teams):
    """How many starters are needed league-wide at each position."""
    flex_share = roster_slots.get("FLEX", 0) * 0.33
    demand = {}
    for pos in _SCORED_POSITIONS:
        slots = roster_slots.get(pos, 0)
        if pos in _FLEX_POSITIONS:
            slots += flex_share
        demand[pos] = int(slots * num_teams)
    return demand


def _position_scarcity(pos, all_at_pos, avail_at_pos, available_ids, demand):
    """Compute scarcity metrics for a single position."""
    total = len(all_at_pos)
    avail_count = len(avail_at_pos)

    # What fraction of the top-N (= demand) players have been drafted
    top_n = max(demand, 1)
    top_players = sorted(all_at_pos, key=lambda x: x["projected_points"], reverse=True)[:top_n]
    top_drafted = sum(1 for p in top_players if p["id"] not in available_ids)
    scarcity_index = top_drafted / top_n

    tier_breakdown = {}
    for p in avail_at_pos:
        t = p.get("tier", 5)
        tier_breakdown[t] = tier_breakdown.get(t, 0) + 1

    alert = _scarcity_alert(pos, avail_at_pos, scarcity_index)

    return {
        "total": total,
        "available": avail_count,
        "drafted": total - avail_count,
        "scarcity_index": round(scarcity_index, 2),
        "tier_breakdown": tier_breakdown,
        "alert": alert,
    }


def _scarcity_alert(pos, avail_at_pos, scarcity_index):
    """Return an alert string if the position is notably scarce, else None."""
    if pos not in _ALERT_POSITIONS:
        return None
    elite_remaining = sum(1 for p in avail_at_pos if p.get("tier", 5) <= _ELITE_TIER_MAX)
    if elite_remaining <= _ELITE_ALERT_THRESHOLD:
        return f"Only {elite_remaining} elite {pos}s remain!"
    if scarcity_index >= _SCARCITY_ALERT_THRESHOLD:
        return f"{pos} position is getting scarce ({int(scarcity_index * 100)}% of starters gone)"
    return None
