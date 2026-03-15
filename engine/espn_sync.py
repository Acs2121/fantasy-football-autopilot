"""
ESPN Fantasy Football live draft sync.
Polls ESPN's draft API every N seconds and auto-records picks into DraftState.
"""

import json
import os
import re
import sys
import threading
import time
import urllib.parse

import requests


# ── Config helpers ─────────────────────────────────────────────────────────────

def _config_path():
    """Return path to espn_config.json.
    When frozen (.exe), place it next to the executable.
    When running from source, place it in the project root.
    """
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), "espn_config.json")
    return os.path.join(os.path.dirname(__file__), "..", "espn_config.json")


_DEFAULT_CONFIG = {
    "league_id": None,
    "year": 2025,
    "swid": "",
    "espn_s2": "",
    "sync_interval_seconds": 5,
}


def load_espn_config():
    path = _config_path()
    if not os.path.exists(path):
        return dict(_DEFAULT_CONFIG)
    with open(path) as f:
        return json.load(f)


def save_espn_config(config):
    with open(_config_path(), "w") as f:
        json.dump(config, f, indent=2)


def _espn_draft_type(espn_type: str) -> str:
    """Map ESPN draft type string to our internal draft_type value."""
    mapping = {
        "SNAKE":   "snake",
        "AUCTION": "auction",
        "SALARY_CAP": "auction",
        "LINEAR":  "snake",
    }
    return mapping.get(espn_type.upper(), "snake")


_SUFFIX_RE = re.compile(r"\s+(jr|sr|ii|iii|iv|v)$")


def _normalize_name(name):
    """Lowercase and strip punctuation for fuzzy name matching."""
    return name.lower().replace(".", "").replace("'", "").replace("-", " ").strip()


def _strip_suffix(name):
    """Remove common name suffixes (Jr, II, etc.) for fallback matching."""
    return _SUFFIX_RE.sub("", name).strip()


# ── ESPNSync ───────────────────────────────────────────────────────────────────

class ESPNSync:

    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Referer": "https://fantasy.espn.com/",
        "Origin": "https://fantasy.espn.com",
    }

    def __init__(self, draft_state):
        self.draft_state = draft_state
        self.config = load_espn_config()
        self.running = False
        self.thread = None
        self.status = "disconnected"
        self.error_msg = None
        self.last_sync = None
        self.league_info = None
        self.espn_draft_complete = False
        self._known_pick_count = 0
        # espn_player_id -> local player id
        self._espn_id_map = {}
        # normalized_name -> local player id
        self._name_map = {
            _normalize_name(p["name"]): p["id"]
            for p in draft_state.all_players
        }
        # espn_team_id -> 0-based team index
        self._team_id_map = {}

    # ── HTTP ───────────────────────────────────────────────────────────────────

    def _cookies(self):
        return {
            "SWID": self.config.get("swid", ""),
            "espn_s2": urllib.parse.unquote(self.config.get("espn_s2", "")),
        }

    def _league_url(self, suffix=""):
        lid = self.config["league_id"]
        year = self.config.get("year", 2025)
        return (
            f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"
            f"/seasons/{year}/segments/0/leagues/{lid}{suffix}"
        )

    def _get(self, url):
        resp = requests.get(
            url, cookies=self._cookies(), headers=self._HEADERS, timeout=10
        )
        resp.raise_for_status()
        return resp.json()

    # ── Connection ─────────────────────────────────────────────────────────────

    def connect(self, league_id=None):
        """Fetch league metadata and build the team map. Returns True on success."""
        if league_id:
            cfg = load_espn_config()
            cfg["league_id"] = int(league_id)
            save_espn_config(cfg)
            self.config = cfg

        if not self.config.get("league_id"):
            self.status = "error"
            self.error_msg = "League ID not set. Enter it in Settings."
            return False

        try:
            data = self._get(self._league_url("?view=mSettings&view=mTeam"))
            teams = self._build_team_map(data.get("teams", []))
            settings = data.get("settings", {})
            ds = settings.get("draftSettings", {})

            self.league_info = {
                "name": settings.get("name", f"League {self.config['league_id']}"),
                "num_teams": len(teams),
                "teams": teams,
                "draft_type": _espn_draft_type(ds.get("type", "SNAKE")),
                "rounds": ds.get("rounds", 15),
                "user_index": self._find_user_index(data.get("teams", [])),
            }
            self.status = "connected"
            self.error_msg = None
            self.espn_draft_complete = False
            return True

        except Exception as e:
            self.status = "error"
            self.error_msg = str(e)
            return False

    def _build_team_map(self, raw_teams):
        """Sort teams by draft order, build _team_id_map, return team list."""
        self._team_id_map = {}
        teams = []
        sorted_teams = sorted(
            raw_teams,
            key=lambda t: t.get("draftDayProjectedRank", t.get("id", 99)),
        )
        for i, t in enumerate(sorted_teams):
            espn_tid = t.get("id")
            self._team_id_map[espn_tid] = i
            teams.append({"espn_id": espn_tid, "name": self._team_name(t, i), "index": i})
        return teams

    @staticmethod
    def _team_name(team, fallback_index):
        """Return the best available display name for an ESPN team dict."""
        name = (team.get("location", "") + " " + team.get("nickname", "")).strip()
        return name or team.get("abbrev", "").strip() or f"Team {fallback_index + 1}"

    def _find_user_index(self, raw_teams):
        """Return the 0-based team index for the authenticated user via SWID."""
        swid = self.config.get("swid", "").upper()
        for t in raw_teams:
            owners = [str(o).upper() for o in t.get("owners", [])]
            if any(swid in o for o in owners):
                return self._team_id_map.get(t.get("id"), 0)
        return 0

    # ── Player matching ────────────────────────────────────────────────────────

    @staticmethod
    def _extract_player_name(pick):
        """Extract fullName from an ESPN pick dict, handling nested structures."""
        ppe = pick.get("playerPoolEntry", {})
        player = ppe.get("playerPoolEntry", {}).get("player", {}) or ppe.get("player", {})
        return player.get("fullName", "")

    def _resolve_player(self, espn_player_id, pick):
        """Map an ESPN player ID to a local player ID, or return None if unknown."""
        if espn_player_id in self._espn_id_map:
            return self._espn_id_map[espn_player_id]

        full_name = self._extract_player_name(pick)
        if full_name:
            norm = _normalize_name(full_name)
            # Direct match, then suffix-stripped fallback (e.g. "Patrick Mahomes II" -> "Patrick Mahomes")
            local_id = self._name_map.get(norm) or self._name_map.get(_strip_suffix(norm))
            if local_id:
                self._espn_id_map[espn_player_id] = local_id
                return local_id

        return None  # Skip rather than record the wrong player

    # ── Sync loop ──────────────────────────────────────────────────────────────

    def _fetch_picks(self):
        """Fetch current draft picks with player name details from ESPN in one request."""
        data = self._get(
            self._league_url("?scoringPeriodId=0&view=mDraftDetail&view=kona_player_info")
        )
        detail = data.get("draftDetail", {})
        return (
            detail.get("picks", []),
            detail.get("drafted", False),
            detail.get("inProgress", False),
        )

    def _sync_picks(self):
        espn_picks, drafted, in_progress = self._fetch_picks()

        if drafted and not in_progress:
            self.espn_draft_complete = True

        new_count = len(espn_picks)
        if new_count <= self._known_pick_count:
            return

        for pick in espn_picks[self._known_pick_count:]:
            espn_pid = pick.get("playerId")
            espn_tid = pick.get("teamId", 1)
            team_index = self._team_id_map.get(
                espn_tid, (espn_tid - 1) % self.draft_state.num_teams
            )
            local_pid = self._resolve_player(espn_pid, pick)
            if local_pid and local_pid in self.draft_state.available_ids:
                self.draft_state.make_pick(local_pid, team_index)

        self._known_pick_count = new_count
        self.last_sync = time.time()

    def _poll_loop(self):
        interval = self.config.get("sync_interval_seconds", 5)
        while self.running:
            try:
                self._sync_picks()
                self.status = "live"
                self.error_msg = None
            except Exception as e:
                self.status = "error"
                self.error_msg = str(e)
            time.sleep(interval)

    # ── Public API ─────────────────────────────────────────────────────────────

    def start_sync(self):
        if self.running:
            return {"ok": True, "msg": "Already syncing"}
        if self.status not in ("connected", "live", "error"):
            if not self.connect():
                return {"ok": False, "msg": self.error_msg}
        self._known_pick_count = 0
        self.running = True
        self.thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.thread.start()
        return {"ok": True, "msg": "Sync started"}

    def stop_sync(self):
        self.running = False
        self.status = "connected"
        return {"ok": True}

    def get_status(self):
        return {
            "status": self.status,
            "error": self.error_msg,
            "league": self.league_info,
            "last_sync": self.last_sync,
            "known_picks": self._known_pick_count,
            "running": self.running,
            "espn_draft_complete": self.espn_draft_complete,
        }
