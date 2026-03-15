"""
CPU draft strategy for draft simulation.

Uses the same 4-factor scoring model as user recommendations:
    score = vbd * need * tier_urgency * survival_urgency * consistency

Selection uses softmax-weighted random choice from the top candidate pool
so CPU teams feel like distinct drafters, not a single deterministic bot.
"""

import math
import random

from engine.recommendations import (
    need_multiplier,
    compute_tier_breaks,
    _tier_break_urgency,
    _survival_probability,
    _survival_urgency,
    _consistency_factor,
    _slot_type,
    _total_starters,
    _fair_value,
)

# -- Tuning constants -------------------------------------------------------

_TOP_CANDIDATES = 8      # Pool size for weighted random selection
_SOFTMAX_TEMP   = 35.0   # Softmax temperature: lower = more deterministic


# -- Public API --------------------------------------------------------------

def select_cpu_pick(draft_state, team_index):
    """Return the player_id the CPU team should draft."""
    available = draft_state.get_available_players()
    if not available:
        return None

    scored = _score_candidates(available, draft_state, team_index)
    return _weighted_pick(scored[:_TOP_CANDIDATES])


def select_cpu_nomination(draft_state, team_index):
    """Pick a player for the CPU team to nominate in an auction."""
    available = draft_state.get_available_players()
    if not available:
        return None

    # Check affordability once (only depends on team budget, not player)
    can_bid = draft_state.can_afford(team_index, draft_state._min_bid)
    affordable = available if not can_bid else available  # all are affordable at min_bid if team can afford it
    # Actually filter: if team can't afford min_bid, use all anyway as fallback
    if can_bid:
        affordable = available
    else:
        affordable = available

    pos_counts   = draft_state.get_positional_counts(team_index)
    roster_slots = draft_state.roster_slots

    if random.random() < 0.5:
        # Self-interested: nominate the CPU's top-scored player
        scored = _score_candidates(affordable, draft_state, team_index)
        return scored[0][1]["id"] if scored else affordable[0]["id"]
    else:
        # Disruptive: nominate a high-VBD player at a position the CPU doesn't need
        filled_positions = {pos for pos, count in pos_counts.items()
                           if count >= roster_slots.get(pos, 0)}
        targets = [p for p in affordable if p["position"] in filled_positions]
        if not targets:
            targets = affordable
        targets.sort(key=lambda x: x.get("vbd_score", 0), reverse=True)
        return random.choice(targets[:5])["id"]


def select_cpu_bid(draft_state, team_index, active_auction):
    """Decide whether the CPU team bids, and for how much. Returns bid or None."""
    player = draft_state.player_map.get(active_auction["player_id"])
    if not player:
        return None

    available  = draft_state.get_available_players()
    starters   = _total_starters(draft_state.roster_slots)
    pos_counts = draft_state.get_positional_counts(team_index)
    need       = need_multiplier(pos_counts, draft_state.roster_slots,
                                 player["position"], starters)

    # Fair value estimation
    total_pos_vbd = sum(max(0, p.get("vbd_score", 0)) for p in available)
    remaining_budget = sum(
        b for i, b in draft_state.team_budgets.items()
        if len(draft_state.team_rosters.get(i, [])) < draft_state.num_rounds
    )
    fair_val = _fair_value(player, total_pos_vbd, remaining_budget, draft_state._min_bid)

    variance    = random.uniform(0.85, 1.15)
    max_willing = max(draft_state._min_bid, round(fair_val * need * variance))

    next_bid = active_auction["current_bid"] + 1
    if next_bid > max_willing or not draft_state.can_afford(team_index, next_bid):
        return None

    return next_bid


# -- Internal helpers --------------------------------------------------------

def _picks_until_team_turn(draft_state, team_index):
    """How many picks until a specific team picks next."""
    if draft_state.draft_complete:
        return -1
    idx = len(draft_state.picks)
    limit = draft_state.num_teams * 2  # max search range (one full snake cycle)
    for offset in range(limit):
        if draft_state.team_for_pick(idx + offset) == team_index:
            return offset
    return draft_state.num_teams


def _score_candidates(available, draft_state, team_index):
    """Score every available player with the full 4-factor model.

    Returns list of (score, player) tuples sorted descending.
    """
    roster_slots = draft_state.roster_slots
    pos_counts   = draft_state.get_positional_counts(team_index)
    current_pick = draft_state.current_pick_number
    starters     = _total_starters(roster_slots)
    is_auction   = draft_state.draft_type == "auction"

    tier_breaks = compute_tier_breaks(available)
    picks_until = 0 if is_auction else _picks_until_team_turn(draft_state, team_index)

    # Cache per-position values
    need_cache = {}
    slot_cache = {}

    scored = []
    for p in available:
        pos = p["position"]
        if pos not in need_cache:
            need_cache[pos] = need_multiplier(pos_counts, roster_slots, pos, starters)
            slot_cache[pos] = _slot_type(pos_counts, roster_slots, pos)

        vbd      = p.get("vbd_score", 0)
        tier_urg = _tier_break_urgency(p, tier_breaks)

        if is_auction or picks_until <= 0:
            surv_urg = 1.0
        else:
            surv_urg = _survival_urgency(_survival_probability(p, current_pick, picks_until))

        cons  = _consistency_factor(p, slot_cache[pos])
        score = max(0.01, vbd * need_cache[pos] * tier_urg * surv_urg * cons)
        scored.append((score, p))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def _weighted_pick(candidates):
    """Softmax-weighted random selection from the candidate pool."""
    if not candidates:
        return None

    scores = [s for s, _ in candidates]
    max_s  = max(scores)
    weights = [math.exp((s - max_s) / _SOFTMAX_TEMP) for s in scores]
    total   = sum(weights)

    r = random.uniform(0, total)
    cumulative = 0.0
    for weight, (_, player) in zip(weights, candidates):
        cumulative += weight
        if r <= cumulative:
            return player["id"]

    return candidates[0][1]["id"]
