"""
ESPN Fantasy Football live draft sync.
Polls ESPN's draft API every N seconds and auto-records picks into DraftState.
"""

import datetime
import json
import os
import re
import sys
import threading
import time
import urllib.parse

import requests


def current_season_year(today=None):
    """Return the NFL season year that is currently relevant.

    The NFL season spans September through February, and ESPN rolls a league
    over to the new seasonId well before kickoff (drafts run through the
    summer). Anything from June onward belongs to the season named after the
    current calendar year; January-May still belongs to the prior season.
    """
    today = today or datetime.date.today()
    return today.year if today.month >= 6 else today.year - 1


# ── Config helpers ─────────────────────────────────────────────────────────────

def _config_path():
    """Return path to espn_config.json.
    When frozen (.exe), place it next to the executable.
    When running from source, place it in the project root.
    """
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), "espn_config.json")
    return os.path.join(os.path.dirname(__file__), "..", "espn_config.json")


def _default_config():
    return {
        "league_id": None,
        "year": current_season_year(),
        "swid": "",
        "espn_s2": "",
        "sync_interval_seconds": 5,
    }


def load_espn_config():
    path = _config_path()
    if not os.path.exists(path):
        return _default_config()
    with open(path) as f:
        cfg = json.load(f)
    # Fill in anything a config written by an older version is missing.
    for key, value in _default_config().items():
        cfg.setdefault(key, value)
    return cfg


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
        self.unresolved_picks = []
        # espn_player_id -> local player id. Seeded from the dataset's own
        # espn_id fields when present, so pick matching is an exact id lookup
        # rather than a name guess. Names remain a fallback for players whose
        # id the dataset doesn't carry.
        self._espn_id_map = {
            str(p["espn_id"]): p["id"]
            for p in draft_state.all_players
            if p.get("espn_id")
        }
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

    _API_BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"

    def _league_url(self, suffix=""):
        """Build the league endpoint for the configured season.

        ESPN serves the *current* season at /seasons/{year}/segments/0/leagues/{id}
        but moves prior seasons to /leagueHistory/{id}?seasonId={year}. Hitting the
        seasons path for a rolled-over season returns 404, which is the single most
        confusing failure mode here — so pick the shape based on the year.
        """
        lid = self.config["league_id"]
        year = int(self.config.get("year") or current_season_year())

        if year >= current_season_year():
            return f"{self._API_BASE}/seasons/{year}/segments/0/leagues/{lid}{suffix}"

        # seasonId always leads the query string; a caller-supplied "?..." suffix
        # becomes "&..." so the two don't collide.
        history_suffix = ("&" + suffix[1:]) if suffix.startswith("?") else suffix
        return f"{self._API_BASE}/leagueHistory/{lid}?seasonId={year}{history_suffix}"

    def _get(self, url):
        try:
            resp = requests.get(
                url, cookies=self._cookies(), headers=self._HEADERS, timeout=10
            )
        except requests.exceptions.Timeout:
            raise RuntimeError("ESPN didn't respond in time. Try again in a moment.")
        except requests.exceptions.RequestException:
            raise RuntimeError(
                "Couldn't reach ESPN. Check your internet connection and try again."
            )

        # Translate ESPN's opaque status codes into something actionable. ESPN
        # returns 404 (not 401/403) for a private league when cookies are missing
        # or stale, so the naive reading -- "wrong league ID" -- is usually wrong.
        if resp.status_code in (401, 403):
            raise RuntimeError(
                "ESPN rejected your credentials (HTTP %d). Your espn_s2 / SWID "
                "cookies are wrong or expired — grab fresh ones and re-enter them."
                % resp.status_code
            )
        if resp.status_code == 404:
            year = int(self.config.get("year") or current_season_year())
            if not (self.config.get("swid") and self.config.get("espn_s2")):
                raise RuntimeError(
                    "ESPN returned 404. For a private league this almost always "
                    "means missing credentials, not a bad League ID — enter your "
                    "SWID and espn_s2 cookies below."
                )
            raise RuntimeError(
                "ESPN returned 404 for league %s in season %d. Check that the "
                "League ID is right and that the league existed that season."
                % (self.config.get("league_id"), year)
            )

        resp.raise_for_status()
        data = resp.json()
        # The leagueHistory endpoint wraps the league object in a list.
        if isinstance(data, list):
            if not data:
                raise RuntimeError("ESPN returned no league data for that season.")
            return data[0]
        return data

    # ── League discovery ───────────────────────────────────────────────────────

    _FAN_API = "https://fan.api.espn.com/apis/v2/fans"

    def list_leagues(self, year=None):
        """Return every fantasy football league on the authenticated account.

        ESPN's "fan" API keys off the SWID and returns the user's followed
        entities, fantasy teams included. Shape is loosely specified and has
        changed before, so parsing here is deliberately defensive: anything that
        doesn't look like an FFL league entry is skipped rather than raising.
        """
        swid = self.config.get("swid", "").strip()
        if not (swid and self.config.get("espn_s2")):
            raise RuntimeError(
                "Enter your SWID and espn_s2 first — ESPN can't list your "
                "leagues without knowing who you are."
            )

        year = int(year or self.config.get("year") or current_season_year())

        url = (
            f"{self._FAN_API}/{urllib.parse.quote(swid)}"
            "?featureFlags=expandAthlete&showAirings=buy,live,replay"
            "&source=ESPN.com+-+FAM&lang=en&section=espn"
        )
        data = self._get(url)

        found = []
        for pref in (data.get("preferences") or []):
            entry = (pref.get("metaData") or {}).get("entry") or {}
            if str(entry.get("abbrev", "")).upper() != "FFL":
                continue
            for group in (entry.get("groups") or []):
                gid = group.get("groupId")
                if gid is None:
                    continue
                found.append({
                    "league_id": int(gid),
                    "name": group.get("groupName") or f"League {gid}",
                    "team_name": entry.get("entryMetadata", {}).get("teamName")
                                 or entry.get("name") or "",
                    "season": int(entry.get("seasonId") or year),
                })

        # A league recurring across seasons appears once per season. Sort newest
        # first, then keep only the most recent entry per league — otherwise the
        # season shown depends on ESPN's arbitrary ordering of `preferences`.
        found.sort(key=lambda l: (-l["season"], l["name"].lower()))
        leagues, seen = [], set()
        for entry in found:
            if entry["league_id"] in seen:
                continue
            seen.add(entry["league_id"])
            leagues.append(entry)
        return leagues

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
        # Exact id match first — no ambiguity, no name normalization involved.
        key = str(espn_player_id)
        if key in self._espn_id_map:
            return self._espn_id_map[key]

        full_name = self._extract_player_name(pick)
        if full_name:
            norm = _normalize_name(full_name)
            # Direct match, then suffix-stripped fallback (e.g. "Patrick Mahomes II" -> "Patrick Mahomes")
            local_id = self._name_map.get(norm) or self._name_map.get(_strip_suffix(norm))
            if local_id:
                self._espn_id_map[key] = local_id
                return local_id

        # Skip rather than record the wrong player — but record the miss, so a
        # silently incomplete draft board is visible instead of invisible.
        label = full_name or f"ESPN id {espn_player_id}"
        if label not in self.unresolved_picks:
            self.unresolved_picks.append(label)
        return None

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
            # Picks ESPN reported that we couldn't match to a player. These are
            # skipped rather than guessed, so surfacing them is the only way the
            # user knows the board is incomplete.
            "unresolved_picks": list(self.unresolved_picks),
        }
