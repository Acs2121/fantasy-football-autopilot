"""
Recommendation engine for draft picks.

Scoring formula (snake/dynasty/best-ball):
    final_score = vbd * need * tier_urgency * survival_urgency * consistency

Each factor occupies a bounded range so no single signal dominates:
    need:              0.3 - 1.5   (smooth curve based on slots remaining)
    tier_urgency:      1.0 - 1.5   (boost when a positional cliff is imminent)
    survival_urgency:  1.0 - 1.5   (boost when player likely gone before next pick)
    consistency:       0.85 - 1.15 (starter slots prefer consistent, bench prefers ceiling)
"""

from engine.rankings import _POS_CONSISTENCY_DEFAULTS

_FLEX_POSITIONS = {"RB", "WR", "TE"}
_STRONG_NEED = 1.5
_DEEP_NEED   = 0.3
_OVERDRAFT_THRESHOLD = 0.6

# Best ball: compressed need range (stacking a position is less punished)
_BB_STRONG_NEED = 1.15
_BB_DEEP_NEED   = 0.85

# Tier-break tuning
_TIER_GAP_THRESHOLD = 2.0   # gaps > median * this count as a tier break
_TIER_URGENCY_MAX   = 1.5   # max boost from a tier break
_TIER_DANGER_ZONE   = 3     # boost kicks in when <= this many players remain in the tier

# Survival probability tuning
_SURVIVAL_URGENCY_MAX = 1.5  # max boost from survival risk

# Consistency tuning
_CONSISTENCY_WEIGHT = 0.3    # strength of consistency factor (0.85-1.15 range)


# -- Need multiplier (smooth curve) ----------------------------------------

def _starter_need_for_position(roster_slots, position):
    """Total starter slots for a position including FLEX eligibility."""
    need = roster_slots.get(position, 0)
    if position in _FLEX_POSITIONS:
        need += roster_slots.get("FLEX", 0)
    return need


def need_multiplier(positional_counts, roster_slots, position, total_starters=None,
                    best_ball=False):
    """Smooth need multiplier that scales linearly from STRONG->DEEP as slots fill."""
    filled = positional_counts.get(position, 0)
    starter_need = _starter_need_for_position(roster_slots, position)

    if total_starters is None:
        total_starters = sum(v for k, v in roster_slots.items() if k != "BN")
    bench = roster_slots.get("BN", 0)
    bench_alloc = max(1, round(bench * starter_need / total_starters)) if total_starters else 0
    total_slots = starter_need + bench_alloc

    remaining = total_slots - filled
    if remaining <= 0:
        return _BB_DEEP_NEED if best_ball else _DEEP_NEED

    hi = _BB_STRONG_NEED if best_ball else _STRONG_NEED
    lo = _BB_DEEP_NEED   if best_ball else _DEEP_NEED
    t  = remaining / total_slots          # 1.0 when empty -> 0.0 when full
    return lo + (hi - lo) * t


def _slot_type(positional_counts, roster_slots, position):
    """Return 'starter' or 'bench' depending on where the next player goes."""
    filled = positional_counts.get(position, 0)
    return "starter" if filled < _starter_need_for_position(roster_slots, position) else "bench"


# -- Tier-break detection --------------------------------------------------

def compute_tier_breaks(available):
    """Find natural VBD break points per position from available players.

    Returns {position: [{rank, gap, players_above_break}]}.
    """
    by_pos = {}
    for p in available:
        by_pos.setdefault(p["position"], []).append(p)

    breaks = {}
    for pos, players in by_pos.items():
        if len(players) < 3:
            breaks[pos] = []
            continue

        sorted_p = sorted(players, key=lambda x: x.get("vbd_score", 0), reverse=True)
        gaps = [
            (i, sorted_p[i].get("vbd_score", 0) - sorted_p[i + 1].get("vbd_score", 0))
            for i in range(len(sorted_p) - 1)
        ]

        gap_values = sorted(g for _, g in gaps)
        median_gap = gap_values[len(gap_values) // 2]
        threshold = max(3.0, median_gap * _TIER_GAP_THRESHOLD)

        breaks[pos] = [
            {"rank": rank_idx, "gap": round(gap, 1), "players_above": rank_idx + 1}
            for rank_idx, gap in gaps
            if gap >= threshold
        ]

    return breaks


def _tier_break_urgency(player, tier_breaks):
    """Urgency multiplier (1.0-1.5) when player is near a value cliff."""
    pos_breaks = tier_breaks.get(player["position"], [])
    if not pos_breaks:
        return 1.0

    for brk in pos_breaks:
        players_above = brk["players_above"]
        if players_above <= _TIER_DANGER_ZONE:
            gap_magnitude = min(1.0, brk["gap"] / 30.0)
            scarcity_factor = 1.0 - (players_above - 1) / _TIER_DANGER_ZONE
            return 1.0 + gap_magnitude * scarcity_factor * (_TIER_URGENCY_MAX - 1.0)

    return 1.0


# -- Survival probability --------------------------------------------------

def _survival_probability(player, current_pick, picks_until_next):
    """Probability (0-1) that player will still be available at the next pick."""
    if picks_until_next <= 0:
        return 1.0

    adp = player.get("adp", 999)
    picks_remaining = adp - current_pick

    if picks_remaining <= 0:
        return max(0.0, 1.0 - 0.2 * picks_until_next)

    ratio = picks_until_next / picks_remaining
    return max(0.0, min(1.0, 1.0 - ratio))


def _survival_urgency(survival_prob):
    """Convert survival probability to an urgency multiplier (1.0-1.5)."""
    return 1.0 + (_SURVIVAL_URGENCY_MAX - 1.0) * (1.0 - survival_prob)


# -- Consistency factor ----------------------------------------------------

def _consistency_factor(player, slot):
    """Modifier based on player consistency and target slot (0.85-1.15)."""
    c = player.get("consistency", _POS_CONSISTENCY_DEFAULTS.get(player["position"], 0.5))
    base = 1.0 - _CONSISTENCY_WEIGHT / 2
    weight_c = c if slot == "starter" else (1.0 - c)
    return base + _CONSISTENCY_WEIGHT * weight_c


# -- Shared scoring helper -------------------------------------------------

def _compute_draft_context(draft_state):
    """Extract draft context values used by the scoring loop."""
    if draft_state is None:
        return 1, 0, False
    return (
        draft_state.current_pick_number,
        draft_state.picks_until_user_turn(),
        getattr(draft_state, "draft_type", "snake") == "auction",
    )


def _total_starters(roster_slots):
    """Count of all starting slots (excluding bench)."""
    return sum(v for k, v in roster_slots.items() if k != "BN")


# -- Main recommendation logic ---------------------------------------------

def get_recommendations(draft_state):
    """Generate pick recommendations for the user's next selection."""
    draft_type = getattr(draft_state, "draft_type", "snake")

    if draft_type == "auction":
        return _auction_recommendations(draft_state)

    available   = draft_state.get_available_players()
    user_counts = draft_state.get_positional_counts(draft_state.user_team_index)
    pick_num    = draft_state.current_pick_number
    is_best_ball = draft_type == "best_ball"

    best_by_need = _score_by_need(
        available, user_counts, draft_state.roster_slots,
        draft_state=draft_state, best_ball=is_best_ball,
    )

    return {
        "best_overall": available[:5],
        "best_by_need": best_by_need[:5],
        "sleeper_picks": _top_sleepers(available, limit=5),
        "overdrafted_warnings": _overdrafted_warnings(available, pick_num, limit=3),
    }


def _score_by_need(available, user_counts, roster_slots,
                   draft_state=None, best_ball=False):
    """Score available players with the full 4-factor model.

    Returns list of (player_dict + need_score) sorted by need_score descending.
    """
    starters = _total_starters(roster_slots)
    tier_breaks = compute_tier_breaks(available)
    current_pick, picks_until, is_auction = _compute_draft_context(draft_state)

    # Pre-compute per-position values (avoid recalculating for every player)
    need_cache = {}
    slot_cache = {}

    scored = []
    for p in available:
        pos = p["position"]

        # Cache need multiplier per position (only depends on position, not player)
        if pos not in need_cache:
            need_cache[pos] = need_multiplier(user_counts, roster_slots, pos, starters,
                                              best_ball=best_ball)
            slot_cache[pos] = _slot_type(user_counts, roster_slots, pos)

        vbd      = p.get("vbd_score", 0)
        need     = need_cache[pos]
        tier_urg = _tier_break_urgency(p, tier_breaks)

        if is_auction or picks_until <= 0:
            surv_urg = 1.0
        else:
            surv_urg = _survival_urgency(_survival_probability(p, current_pick, picks_until))

        cons  = _consistency_factor(p, slot_cache[pos])
        final = round(vbd * need * tier_urg * surv_urg * cons, 1)
        scored.append({**p, "need_score": final})

    scored.sort(key=lambda x: x["need_score"], reverse=True)
    return scored


def _top_sleepers(available, limit=5):
    """Return available players with a meaningful sleeper score, best first."""
    sleepers = [p for p in available if p.get("sleeper_score", 0) >= 0.05]
    sleepers.sort(key=lambda x: x["sleeper_score"], reverse=True)
    return sleepers[:limit]


def _overdrafted_warnings(available, pick_num, limit=3):
    """Return players whose ADP was much earlier than the current pick number."""
    return [
        p for p in available
        if p.get("adp", 999) < pick_num * _OVERDRAFT_THRESHOLD
    ][:limit]


# -- Auction recommendations -----------------------------------------------

def _fair_value(player, total_pos_vbd, remaining_budget, min_bid):
    """Estimate fair dollar value for a player in an auction."""
    vbd   = max(0, player.get("vbd_score", 0))
    share = vbd / total_pos_vbd if total_pos_vbd > 0 else 0
    return max(min_bid, round(share * remaining_budget))


def _auction_recommendations(draft_state):
    """Auction-specific recommendations: bid guidance + value targets."""
    available      = draft_state.get_available_players()
    user_counts    = draft_state.get_positional_counts(draft_state.user_team_index)
    roster_slots   = draft_state.roster_slots
    user_budget    = draft_state.team_budgets.get(draft_state.user_team_index, 0)
    starters       = _total_starters(roster_slots)
    min_bid        = draft_state._min_bid

    total_pos_vbd = sum(max(0, p.get("vbd_score", 0)) for p in available)
    remaining_budget = sum(
        b for i, b in draft_state.team_budgets.items()
        if len(draft_state.team_rosters.get(i, [])) < draft_state.num_rounds
    )

    # Compute estimated values once
    ev_map = {}
    for p in available:
        ev_map[p["id"]] = _fair_value(p, total_pos_vbd, remaining_budget, min_bid)

    valued = [{**p, "est_value": ev_map[p["id"]]} for p in available]
    best_value = sorted(valued, key=lambda x: x["est_value"], reverse=True)[:5]

    best_by_need = _score_by_need(available, user_counts, roster_slots,
                                  draft_state=draft_state)[:5]
    for p in best_by_need:
        p["est_value"] = ev_map.get(p["id"], 0)

    # Bargain targets: est_value vs ADP-implied price
    total_picks = draft_state.num_teams * draft_state.num_rounds
    budget_pt   = draft_state._budget_per_team
    bargains = []
    for p in valued:
        adp = p.get("adp", total_picks)
        adp_price = max(1, round((1 - adp / total_picks) * budget_pt * 0.8))
        surplus   = p["est_value"] - adp_price
        if surplus >= 3 and p["est_value"] >= 5:
            bargains.append({**p, "surplus": surplus, "adp_price": adp_price})
    bargains.sort(key=lambda x: x["surplus"], reverse=True)

    # Active auction bid guidance
    bid_guidance = None
    active = draft_state.active_auction
    if active:
        pid    = active["player_id"]
        player = draft_state.player_map.get(pid)
        if player:
            ev      = _fair_value(player, total_pos_vbd, remaining_budget, min_bid)
            need    = need_multiplier(user_counts, roster_slots, player["position"], starters)
            max_bid = min(max(min_bid, round(ev * need)), user_budget)
            current = active["current_bid"]
            bid_guidance = {
                "player":      player,
                "current_bid": current,
                "est_value":   ev,
                "max_bid":     max_bid,
                "verdict":     ("good deal" if current < ev * 0.85 else
                                "fair"      if current <= ev       else "overpay"),
            }

    return {
        "best_overall":          best_value,
        "best_by_need":          best_by_need,
        "sleeper_picks":         bargains[:5],
        "overdrafted_warnings":  [],
        "bid_guidance":          bid_guidance,
        "is_auction":            True,
    }
