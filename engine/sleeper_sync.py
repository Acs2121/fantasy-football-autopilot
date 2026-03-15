"""
Sleeper API integration — fetches fresh ADP and projected points for the
current season and patches them onto the in-memory player list.

Endpoints used (no auth required):
  GET https://api.sleeper.app/v1/players/nfl
      → full player metadata including name, position, ESPN player ID
  GET https://api.sleeper.app/v1/projections/nfl/regular/{year}
      → season projections: adp_half_ppr, pts_half_ppr per player

Player matching priority:
  1. ESPN player ID (most reliable)
  2. Normalized full name (lowercase, letters/spaces only)
"""

import re
import urllib.request
import json
import logging

logger = logging.getLogger(__name__)

_SLEEPER_BASE = "https://api.sleeper.app/v1"
_TIMEOUT      = 10   # seconds per HTTP call


# ── Public API ──────────────────────────────────────────────────────────────

def refresh_player_data(draft_state, year: int = 2025) -> dict:
    """
    Pull fresh ADP + projected points from Sleeper and patch draft_state's
    player list in place.

    Returns a summary dict:
        { "updated": int, "total": int, "error": str|None }
    """
    try:
        sleeper_players  = _fetch_json(f"{_SLEEPER_BASE}/players/nfl")
        sleeper_proj     = _fetch_json(f"{_SLEEPER_BASE}/projections/nfl/regular/{year}")
    except Exception as exc:
        logger.error("Sleeper fetch failed: %s", exc)
        return {"updated": 0, "total": 0, "error": str(exc)}

    # Build lookup maps from Sleeper data
    espn_id_map, name_map = _build_sleeper_maps(sleeper_players, sleeper_proj)

    updated = 0
    total   = len(draft_state.player_map)

    for pid, player in draft_state.player_map.items():
        proj = _lookup_projection(player, espn_id_map, name_map)
        if proj is None:
            continue

        adp  = proj.get("adp_half_ppr")
        pts  = proj.get("pts_half_ppr")

        changed = False
        if adp is not None and adp > 0:
            player["adp"] = round(float(adp), 1)
            changed = True
        if pts is not None and pts > 0:
            player["projected_points"] = round(float(pts), 1)
            changed = True

        if changed:
            updated += 1

    # Rebuild VBD scores since projected_points changed
    if updated > 0:
        _recompute_vbd(draft_state)

    return {"updated": updated, "total": total, "error": None}


# ── Internal helpers ─────────────────────────────────────────────────────────

def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "FantasyFootballAutopilot/1.0"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def _normalize(name: str) -> str:
    """Lowercase, keep only letters and spaces, collapse whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", "", name.lower())).strip()


def _build_sleeper_maps(sleeper_players: dict, sleeper_proj: dict):
    """
    Returns:
        espn_id_map  — { espn_id_str : projection_dict }
        name_map     — { normalized_name : projection_dict }
    """
    espn_id_map = {}
    name_map    = {}

    for sleeper_id, meta in sleeper_players.items():
        proj = sleeper_proj.get(sleeper_id)
        if not proj:
            continue

        espn_id = meta.get("espn_id")
        if espn_id:
            espn_id_map[str(espn_id)] = proj

        fname = meta.get("first_name", "")
        lname = meta.get("last_name", "")
        full  = f"{fname} {lname}".strip()
        if full:
            name_map[_normalize(full)] = proj

    return espn_id_map, name_map


def _lookup_projection(player: dict, espn_id_map: dict, name_map: dict):
    """Find the Sleeper projection for a local player dict."""
    # Try ESPN ID first
    espn_id = player.get("espn_id")
    if espn_id and str(espn_id) in espn_id_map:
        return espn_id_map[str(espn_id)]

    # Fall back to name match
    norm = _normalize(player.get("name", ""))
    return name_map.get(norm)


def _recompute_vbd(draft_state):
    """
    Re-derive VBD scores after projected_points have been updated.
    Mirrors the logic in engine/rankings.py so we don't need to reload JSON.
    VBD = projected_points - baseline, where baseline is the projected_points
    of the last starter at that position (positional_rank == roster_slot_count).
    """
    from engine.rankings import compute_vbd  # lazy import to avoid circulars

    try:
        compute_vbd(
            list(draft_state.player_map.values()),
            draft_state.roster_slots,
            draft_state.num_teams,
        )
    except Exception as exc:
        logger.warning("VBD recompute skipped: %s", exc)
