import re

from flask import Flask, jsonify, request, render_template
from config import DEFAULT_CONFIG
from engine.draft import DraftState
from engine.recommendations import get_recommendations
from engine.scarcity import compute_scarcity
from engine.win_probability import compute_win_probability
from engine.espn_sync import ESPNSync, load_espn_config, save_espn_config
from engine.espn_auth import BrowserLogin, detect_browser_cookies
from engine.rebuild_job import RebuildJob
from engine.cpu_strategy import select_cpu_pick, select_cpu_nomination, select_cpu_bid
from engine.sleeper_sync import refresh_player_data
from engine.nfl_stats import fetch_and_cache, force_refresh as nfl_force_refresh
from engine.season import current_season_year, last_completed_season
from engine.rankings import dataset_season
from engine.stat_processor import process_all_players

app = Flask(__name__)
draft = DraftState()
espn = ESPNSync(draft)
# Survives _rebuild_draft() on purpose — a sign-in in progress shouldn't be
# cancelled just because the draft was reconfigured.
espn_login = BrowserLogin()
# Full data refresh runs in the background — it downloads several MB.
_refresh_job = RebuildJob()

# Name normalization for NFL stats matching (module-level, compiled once)
_SUFFIXES_RE = re.compile(r"\s+(jr|sr|ii|iii|iv|v)$")
_COMPONENT_KEYS = (
    "base_value", "volume_score", "efficiency_score", "td_score",
    "team_env_score", "consistency_score", "ceiling_score", "risk_score",
    "consistency",
)


def _normalize_stat_name(name):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", "", name.lower())).strip()


def _strip_suffix(name):
    return _SUFFIXES_RE.sub("", name).strip()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _rebuild_draft(config, *, restart_sync=False):
    """Replace the global draft + espn pair with fresh instances.

    Stops any active sync first. If restart_sync is True, reconnects to ESPN
    and resumes polling after the rebuild (used by reset/configure when sync
    was already running).
    """
    global draft, espn
    espn.stop_sync()
    draft = DraftState(config)
    espn = ESPNSync(draft)
    if restart_sync:
        espn.connect()
        espn.start_sync()


def _data_status():
    """Whether the loaded player dataset matches the season being drafted.

    Stale projections are not wrong-looking — they look perfectly plausible —
    so the app has to say so rather than let them pass as current.
    """
    have = dataset_season()
    want = current_season_year()
    return {
        "dataset_season": have,
        "current_season": want,
        "stale": have is not None and have < want,
    }


def _apply_stat_components(year, scoring="PPR", force=False):
    """Pull nflverse stats, score components, and patch them onto the pool.

    Shared by the stats route and the full-refresh job.
    """
    raw = nfl_force_refresh(year) if force else fetch_and_cache(year)
    scored = process_all_players(raw, scoring_format=scoring)
    name_map = {_normalize_stat_name(k): v for k, v in scored.items()}
    updated = 0

    for player in draft.player_map.values():
        norm = _normalize_stat_name(player.get("name", ""))
        comp = name_map.get(norm) or name_map.get(_strip_suffix(norm))
        if comp is not None:
            for key in _COMPONENT_KEYS:
                player[key] = comp[key]
            player["games_played_last_season"] = comp["games_played"]
            updated += 1
        else:
            # No stats for this player (rookie, kicker, team defense) — fall
            # back to the projection rather than inventing a component score.
            player["base_value"] = player.get("projected_points", 0)

    if updated > 0:
        from engine.rankings import compute_vbd
        compute_vbd(list(draft.player_map.values()), draft.roster_slots, draft.num_teams)

    total = len(draft.player_map)
    return {"updated": updated, "total": total,
            "stats_players": len(scored), "fallback": total - updated}


def _error_response(result):
    """Return (jsonify(result), 400) if result contains an error key."""
    return jsonify(result), (400 if "error" in result else 200)


# ── Draft routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def get_state():
    state = draft.get_state_dict()
    if espn.league_info and espn.league_info.get("teams"):
        state["espn_team_names"] = {
            t["index"]: t["name"] for t in espn.league_info["teams"]
        }
    return jsonify(state)


@app.route("/api/available")
def get_available():
    pos = request.args.get("pos")
    limit = int(request.args.get("limit", 200))
    return jsonify(draft.get_available_players(position=pos)[:limit])


@app.route("/api/pick", methods=["POST"])
def make_pick():
    data = request.get_json()
    player_id = data.get("player_id")
    if player_id is None:
        return jsonify({"error": "player_id required"}), 400
    return _error_response(draft.make_pick(player_id, data.get("team_index")))


@app.route("/api/undo", methods=["POST"])
def undo_pick():
    return _error_response(draft.undo_last_pick())


@app.route("/api/recommendations")
def recommendations():
    return jsonify(get_recommendations(draft))


@app.route("/api/scarcity")
def scarcity():
    return jsonify(compute_scarcity(draft))


@app.route("/api/win-probability")
def win_probability():
    return jsonify(compute_win_probability(draft))


@app.route("/api/dashboard")
def dashboard():
    """Consolidated endpoint — replaces 4 separate calls per refresh cycle."""
    state = draft.get_state_dict()
    if espn.league_info and espn.league_info.get("teams"):
        state["espn_team_names"] = {
            t["index"]: t["name"] for t in espn.league_info["teams"]
        }
    return jsonify({
        "data_status": _data_status(),
        "state": state,
        "recommendations": get_recommendations(draft),
        "scarcity": compute_scarcity(draft),
        "win_probability": compute_win_probability(draft),
    })


@app.route("/api/reset", methods=["POST"])
def reset_draft():
    _rebuild_draft(dict(DEFAULT_CONFIG), restart_sync=espn.running)
    return jsonify({"status": "ok"})


@app.route("/api/configure", methods=["POST"])
def configure():
    data = request.get_json()
    was_syncing = espn.running

    config = dict(DEFAULT_CONFIG)
    if "draft_type" in data:
        config["draft_type"] = data["draft_type"]
    for field in ("num_teams", "num_rounds", "user_team_index"):
        if field in data:
            config[field] = int(data[field])
    if "snake" in data:
        config["snake"] = bool(data["snake"])
    if "roster_slots" in data:
        config["roster_slots"] = data["roster_slots"]

    _rebuild_draft(config, restart_sync=was_syncing)
    return jsonify({"status": "ok", "config": config})


@app.route("/api/simulate", methods=["POST"])
def simulate_pick():
    """Auto-pick / auto-bid for the current CPU team."""
    if draft.draft_complete:
        return jsonify({"error": "Draft complete"}), 400
    if draft.is_user_pick:
        return jsonify({"error": "user_turn", "msg": "It's your pick"}), 400

    if draft.draft_type == "auction":
        return _simulate_auction_step()

    # Standard snake / best ball / dynasty
    player_id = select_cpu_pick(draft, draft.current_team_index)
    if player_id is None:
        return jsonify({"error": "No players available"}), 400
    result = draft.make_pick(player_id)
    return jsonify({**result, "player": draft.player_map[player_id]})


def _simulate_auction_step():
    """Run one CPU auction step: either nominate a player or place/pass a bid."""
    if draft.active_auction is None:
        # CPU nomination turn
        team = draft.current_nomination_team
        if team == draft.user_team_index:
            return jsonify({"error": "user_turn", "msg": "Your turn to nominate"}), 400
        pid = select_cpu_nomination(draft, team)
        if pid is None:
            return jsonify({"error": "No players available"}), 400
        result = draft.start_nomination(pid, draft._min_bid, team)
        if "error" in result:
            return jsonify(result), 400

    # Active auction — run CPU bids until everyone has responded
    active = draft.active_auction
    if active is None:
        return jsonify({"ok": True, "msg": "Auction closed"})

    # CPU teams that still need to respond (not passed, not current high bidder)
    cpu_pending = draft._all_teams_set - active["passed"] - {active["current_bidder"]} - {draft.user_team_index}

    for team in list(cpu_pending):
        if draft.active_auction is None:
            break
        bid_amount = select_cpu_bid(draft, team, draft.active_auction)
        if bid_amount is not None:
            draft.place_bid(team, bid_amount)
        else:
            result = draft.pass_bid(team)
            if result.get("ok") and not result.get("waiting"):
                # Auction closed by this pass
                return jsonify({"ok": True, "closed": True, "result": result.get("result")})

    return jsonify({"ok": True, "auction": draft._auction_dict()})


# ── Auction-specific routes ────────────────────────────────────────────────────

@app.route("/api/auction/nominate", methods=["POST"])
def auction_nominate():
    """User nominates a player to start an auction."""
    if draft.draft_type != "auction":
        return jsonify({"error": "Not in auction mode"}), 400
    data = request.get_json() or {}
    player_id   = data.get("player_id")
    opening_bid = int(data.get("opening_bid", draft._min_bid))
    if player_id is None:
        return jsonify({"error": "player_id required"}), 400
    result = draft.start_nomination(player_id, opening_bid, draft.user_team_index)
    if "error" in result:
        return jsonify(result), 400

    # Immediately have CPU teams respond
    _run_cpu_bids()
    return jsonify({"ok": True, "auction": draft._auction_dict()})


@app.route("/api/auction/bid", methods=["POST"])
def auction_bid():
    """User places a bid on the active auction."""
    if draft.active_auction is None:
        return jsonify({"error": "No active auction"}), 400
    data   = request.get_json() or {}
    amount = data.get("amount")
    if amount is None:
        return jsonify({"error": "amount required"}), 400
    result = draft.place_bid(draft.user_team_index, int(amount))
    if "error" in result:
        return jsonify(result), 400

    # CPU teams respond
    _run_cpu_bids()
    return jsonify({"ok": True, "auction": draft._auction_dict(), "closed": draft.active_auction is None})


@app.route("/api/auction/pass", methods=["POST"])
def auction_pass():
    """User passes on the current auction."""
    if draft.active_auction is None:
        return jsonify({"error": "No active auction"}), 400
    result = draft.pass_bid(draft.user_team_index)
    if "error" in result:
        return jsonify(result), 400

    if not result.get("waiting"):
        # Auction just closed
        return jsonify({"ok": True, "closed": True, "result": result.get("result")})

    _run_cpu_bids()
    return jsonify({"ok": True, "auction": draft._auction_dict(), "closed": draft.active_auction is None})


def _run_cpu_bids():
    """Let all CPU teams place bids or pass on the active auction."""
    if draft.active_auction is None:
        return
    # Keep iterating until no CPU team changes the state
    max_rounds = draft.num_teams * 2
    for _ in range(max_rounds):
        if draft.active_auction is None:
            break
        active = draft.active_auction
        cpu_pending = draft._all_teams_set - active["passed"] - {active["current_bidder"]} - {draft.user_team_index}
        if not cpu_pending:
            break
        changed = False
        for team in list(cpu_pending):
            if draft.active_auction is None:
                break
            bid_amount = select_cpu_bid(draft, team, draft.active_auction)
            if bid_amount is not None:
                draft.place_bid(team, bid_amount)
                changed = True
            else:
                result = draft.pass_bid(team)
                if result.get("ok") and not result.get("waiting"):
                    return  # auction closed
                changed = True
        if not changed:
            break


@app.route("/api/keepers", methods=["GET"])
def get_keepers():
    """Return current keeper assignments (dynasty mode)."""
    return jsonify({
        "draft_type": draft.draft_type,
        "keeper_player_ids": list(draft.keeper_player_ids),
        "keepers": {
            str(i): draft.team_rosters[i][:draft._keeper_slots]
            for i in range(draft.num_teams)
            if draft._keeper_slots > 0
        },
    })


@app.route("/api/keepers", methods=["POST"])
def set_keepers():
    """Assign keeper players to teams before the draft starts (dynasty mode only)."""
    if draft.draft_type != "dynasty":
        return jsonify({"error": "Only available in dynasty mode"}), 400
    if draft.picks:
        return jsonify({"error": "Cannot reassign keepers after the draft has started"}), 400
    data = request.get_json() or {}
    keepers = data.get("keepers", {})  # {team_index: [player_id, ...]}
    if not isinstance(keepers, dict):
        return jsonify({"error": "keepers must be a dict of team_index: [player_ids]"}), 400

    # Reset any previously loaded keepers
    for pid in list(draft.keeper_player_ids):
        draft.available_ids.add(pid)
        if "is_keeper" in draft.player_map.get(pid, {}):
            draft.player_map[pid].pop("is_keeper", None)
    for i in range(draft.num_teams):
        draft.team_rosters[i] = []
    draft.keeper_player_ids = set()

    draft.load_keepers(keepers)
    return jsonify({
        "ok": True,
        "keeper_player_ids": list(draft.keeper_player_ids),
        "total_keepers": len(draft.keeper_player_ids),
    })


@app.route("/api/sleeper/refresh", methods=["POST"])
def sleeper_refresh():
    """Pull fresh ADP + projected points from Sleeper and recompute VBD."""
    data = request.get_json() or {}
    # Defaults to the season being drafted, not a frozen year.
    year = int(data.get("year") or current_season_year())
    summary = refresh_player_data(draft, year=year)
    if summary["error"]:
        return jsonify({"ok": False, "error": summary["error"]}), 502
    return jsonify({"ok": True, "updated": summary["updated"], "total": summary["total"]})


@app.route("/api/stats/refresh", methods=["POST"])
def stats_refresh():
    """Fetch NFL stats from nflverse, compute component scores, patch players."""
    data = request.get_json() or {}
    # Historical stats come from the last completed season.
    year = int(data.get("year") or last_completed_season())
    scoring = data.get("scoring_format", "PPR")
    do_force = data.get("force", False)

    try:
        result = _apply_stat_components(year, scoring, force=do_force)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502
    return jsonify({"ok": True, **result})


@app.route("/api/stats/components/<int:player_id>")
def stats_components(player_id):
    """Return component score breakdown for a specific player."""
    player = draft.player_map.get(player_id)
    if not player:
        return jsonify({"error": "Player not found"}), 404
    return jsonify({
        "id": player_id,
        "name": player.get("name"),
        "position": player.get("position"),
        "base_value": player.get("base_value"),
        "volume_score": player.get("volume_score"),
        "efficiency_score": player.get("efficiency_score"),
        "td_score": player.get("td_score"),
        "team_env_score": player.get("team_env_score"),
        "consistency_score": player.get("consistency_score"),
        "ceiling_score": player.get("ceiling_score"),
        "risk_score": player.get("risk_score"),
    })


# ── ESPN sync routes ───────────────────────────────────────────────────────────

@app.route("/api/espn/status")
def espn_status():
    return jsonify(espn.get_status())


@app.route("/api/espn/connect", methods=["POST"])
def espn_connect():
    data = request.get_json() or {}
    ok = espn.connect(league_id=data.get("league_id"))
    if not ok:
        return jsonify({"ok": False, "error": espn.error_msg}), 400

    info = espn.league_info
    if info:
        # Snapshot connection state before rebuilding so we don't lose it
        saved_league_info = espn.league_info
        saved_team_id_map = espn._team_id_map
        saved_config = espn.config

        config = dict(DEFAULT_CONFIG)
        espn_dtype = info["draft_type"]   # already mapped to "snake"/"auction" by _espn_draft_type
        config.update({
            "draft_type": espn_dtype,
            "num_teams": info["num_teams"],
            "num_rounds": info["rounds"],
            "user_team_index": info["user_index"],
            "snake": espn_dtype == "snake",
        })
        _rebuild_draft(config, restart_sync=False)

        # Restore connection state onto the new ESPN instance
        espn.league_info = saved_league_info
        espn._team_id_map = saved_team_id_map
        espn.status = "connected"
        espn.config = saved_config

    return jsonify({"ok": True, "league": info, "auto_configured": bool(info)})


@app.route("/api/espn/start", methods=["POST"])
def espn_start():
    return jsonify(espn.start_sync())


@app.route("/api/espn/stop", methods=["POST"])
def espn_stop():
    return jsonify(espn.stop_sync())


@app.route("/api/data/refresh", methods=["POST"])
def data_refresh():
    """One full refresh: rebuild the player list, then rescore it.

    Runs every data source end to end --
      1. nflverse rosters   who is on which team this season, plus rookies
                            and the espn_id/sleeper_id keys ESPN sync matches on
      2. nflverse schedule  bye weeks derived from the real fixture list
      3. Sleeper            ADP and season projections
      4. nflverse stats     last completed season's production -> component scores

    Backgrounded: it downloads several MB and takes a few minutes.
    """
    if _refresh_job.is_running():
        return jsonify({"ok": True, "already_running": True})

    def work(progress):
        import json as _json, os as _os
        import rebuild_players as rb

        year = current_season_year()
        stats_year = last_completed_season()

        progress("Rebuilding player list from live sources...")
        players, report = rb.build(year, stats_year)

        # Refuse to replace a working dataset with a broken one.
        if not players or len(players) < 150:
            raise RuntimeError(
                f"Only {len(players)} players came back — too few to be right. "
                "Your existing data file was not touched."
            )

        _os.makedirs(rb.DATA_DIR, exist_ok=True)
        out = _os.path.join(rb.DATA_DIR, f"players_{year}.json")
        with open(out, "w") as f:
            _json.dump(players, f, indent=1)

        progress("Loading the new player list...")
        _rebuild_draft(dict(DEFAULT_CONFIG), restart_sync=espn.running)

        progress("Scoring players against last season's production...")
        stats = _apply_stat_components(stats_year)

        counts = ", ".join(f"{k} {v}" for k, v in (report.get("by_position") or {}).items())
        report["stats"] = stats
        report["summary"] = (
            f"Refreshed {len(players)} players for {year} ({counts}). "
            f"{stats['updated']} scored from {stats_year} stats."
        )
        return report

    _refresh_job.start(work)
    return jsonify({"ok": True})


@app.route("/api/data/refresh/status")
def data_refresh_status():
    return jsonify(_refresh_job.status())


@app.route("/api/espn/auth/detect", methods=["POST"])
def espn_auth_detect():
    """Try to find an existing ESPN session in the local browser profiles."""
    pair = detect_browser_cookies()
    if not pair:
        return jsonify({
            "ok": False,
            "error": "No existing ESPN session found in your browsers.",
        }), 404

    swid, s2 = pair
    cfg = load_espn_config()
    cfg["swid"], cfg["espn_s2"] = swid, s2
    save_espn_config(cfg)
    espn.config = cfg
    return jsonify({"ok": True, "source": "browser"})


@app.route("/api/espn/auth/login", methods=["POST"])
def espn_auth_login():
    """Open ESPN's real login page in a browser window (non-blocking)."""
    if espn_login.is_running():
        return jsonify({"ok": True, "already_running": True})
    espn_login.start()
    return jsonify({"ok": True})


@app.route("/api/espn/auth/status")
def espn_auth_status():
    """Poll target for the login window. Saves credentials once they arrive."""
    status = espn_login.status()

    if status["state"] == "success" and espn_login.result:
        swid, s2 = espn_login.result
        cfg = load_espn_config()
        cfg["swid"], cfg["espn_s2"] = swid, s2
        save_espn_config(cfg)
        espn.config = cfg

    return jsonify(status)


@app.route("/api/espn/auth/cancel", methods=["POST"])
def espn_auth_cancel():
    espn_login.cancel()
    return jsonify({"ok": True})


@app.route("/api/espn/leagues", methods=["POST"])
def espn_list_leagues():
    """List every FFL league on the authenticated ESPN account.

    Accepts credentials in the body so the UI can look leagues up before
    anything has been saved to disk.
    """
    data = request.get_json() or {}
    cfg = load_espn_config()
    for field in ("swid", "espn_s2"):
        if data.get(field):
            cfg[field] = data[field]
    if data.get("year"):
        cfg["year"] = int(data["year"])
    espn.config = cfg

    try:
        leagues = espn.list_leagues()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    if not leagues:
        return jsonify({
            "ok": False,
            "error": "ESPN returned no fantasy football leagues for this account. "
                     "Double-check the SWID, or enter the League ID manually.",
        }), 404

    return jsonify({"ok": True, "leagues": leagues})


@app.route("/api/espn/config", methods=["GET"])
def espn_get_config():
    cfg = load_espn_config()
    return jsonify({
        "league_id": cfg.get("league_id"),
        "year": cfg.get("year"),
        "has_credentials": bool(cfg.get("swid") and cfg.get("espn_s2")),
    })


@app.route("/api/espn/config", methods=["POST"])
def espn_set_config():
    data = request.get_json() or {}
    cfg = load_espn_config()

    if "league_id" in data:
        cfg["league_id"] = int(data["league_id"]) if data["league_id"] else None
    for field in ("swid", "espn_s2"):
        if field in data:
            cfg[field] = data[field]
    if "year" in data:
        cfg["year"] = int(data["year"])

    save_espn_config(cfg)
    espn.config = cfg
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(debug=True, port=5000, use_reloader=False)
