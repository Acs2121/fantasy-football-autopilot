import math

_POSITIONS         = ["QB", "RB", "WR", "TE", "K", "DST"]
_FLEX_POSITIONS    = {"RB", "WR", "TE"}
_STD_DEV_FACTOR    = 0.12   # ~12% variance around league average, empirically reasonable
_WIN_PROB_MIN      = 5.0
_WIN_PROB_MAX      = 95.0


def compute_win_probability(draft_state):
    """Estimate win probability based on roster strength vs league average."""
    user_roster = draft_state.get_team_roster(draft_state.user_team_index)
    if not user_roster:
        return {"win_probability": 50.0, "projected_points": 0, "league_avg": 0, "starters": []}

    roster_slots = draft_state.roster_slots
    starters = _select_best_starters(user_roster, roster_slots)
    team_points = sum(p["projected_points"] for p in starters)
    league_avg = _estimate_league_average(draft_state.all_players, roster_slots, draft_state.num_teams)

    win_prob = _sigmoid_win_prob(team_points, league_avg)

    return {
        "win_probability": win_prob,
        "projected_points": round(team_points, 1),
        "league_avg": round(league_avg, 1),
        "starters": starters,
    }


def _select_best_starters(roster, roster_slots):
    """Greedily assign best players to starting lineup slots."""
    available = sorted(roster, key=lambda x: x["projected_points"], reverse=True)
    used = set()
    starters = []

    for pos in _POSITIONS:
        needed = roster_slots.get(pos, 0)
        filled = 0
        for p in available:
            if filled >= needed:
                break
            if p["id"] not in used and p["position"] == pos:
                starters.append(p)
                used.add(p["id"])
                filled += 1

    flex_needed = roster_slots.get("FLEX", 0)
    filled = 0
    for p in available:
        if filled >= flex_needed:
            break
        if p["id"] not in used and p["position"] in _FLEX_POSITIONS:
            starters.append(p)
            used.add(p["id"])
            filled += 1

    return starters


def _estimate_league_average(all_players, roster_slots, num_teams):
    """Estimate average team projected points across the league."""
    by_pos = {}
    for p in all_players:
        by_pos.setdefault(p["position"], []).append(p)

    total = 0.0
    for pos in _POSITIONS:
        slots = roster_slots.get(pos, 0)
        if pos in _FLEX_POSITIONS:
            slots += roster_slots.get("FLEX", 0) * 0.33
        top_n = int(slots * num_teams)
        pos_players = sorted(
            by_pos.get(pos, []),
            key=lambda x: x["projected_points"],
            reverse=True,
        )
        total += sum(p["projected_points"] for p in pos_players[:top_n])

    return total / max(num_teams, 1)


def _sigmoid_win_prob(team_points, league_avg):
    """Convert point differential vs league average to a win probability."""
    std_dev = league_avg * _STD_DEV_FACTOR
    z = (team_points - league_avg) / std_dev if std_dev > 0 else 0
    prob = 1 / (1 + math.exp(-z)) * 100
    return max(_WIN_PROB_MIN, min(_WIN_PROB_MAX, round(prob, 1)))
