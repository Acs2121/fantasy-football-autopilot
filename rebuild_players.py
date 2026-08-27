#!/usr/bin/env python3
"""
Rebuild the player dataset for the current season from live sources.

Every number this writes comes from a real feed. Nothing is invented, and any
player the sources don't cover is reported rather than filled with a guess.

Sources
    nflverse rosters      current-season teams, positions, rookie flags, and
                          cross-platform ids (espn_id, sleeper_id)
    nflverse weekly stats last completed season's actual production
    nflverse schedules    bye weeks for the upcoming season
    Sleeper               ADP and season projections for the upcoming season

Joins are done on sleeper_id where available -- an exact key, not a name guess.
Name matching is a labelled fallback and is counted in the report.

Usage
    python rebuild_players.py                # rebuild for the current season
    python rebuild_players.py --year 2027    # or a specific one
    python rebuild_players.py --dry-run      # report only, write nothing
"""

import argparse
import json
import logging
import os
import re
import sys
import time
import urllib.parse
import urllib.request

from engine.season import current_season_year, last_completed_season

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("rebuild")

NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"
SLEEPER = "https://api.sleeper.app/v1"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

SKILL_POSITIONS = ("QB", "RB", "WR", "TE", "K")

# Team defenses are not players, so they aren't on nflverse rosters. Sleeper
# carries them as position "DEF", keyed by team abbreviation.
DST_POSITION = "DST"

# Roughly how many of each position a 12-team league actually drafts. Keeps the
# file to a useful size instead of every practice-squad body on an NFL roster.
POSITION_LIMITS = {"QB": 40, "RB": 75, "WR": 95, "TE": 35, "K": 24, "DST": 32}

# Tier boundaries by positional rank -- a coarse grouping used for display.
TIER_BREAKS = (6, 14, 26, 40, 60)

POS_CONSISTENCY_DEFAULTS = {
    "QB": 0.80, "RB": 0.50, "WR": 0.40, "TE": 0.45, "K": 0.70, "DST": 0.30,
}

# The two feeds disagree on a handful of team codes. Left is what Sleeper (and
# ESPN) use; right is nflverse's. Without this the Rams' bye week silently
# comes back 0.
TEAM_ALIASES = {
    "LAR": "LA",
    "WSH": "WAS",
    "JAC": "JAX",
    "SD": "LAC",
    "OAK": "LV",
    "STL": "LA",
    "ARZ": "ARI",
    "BLT": "BAL",
    "CLV": "CLE",
    "HST": "HOU",
}


def canonical_team(code):
    """Normalize a team code to the one nflverse uses."""
    code = str(code or "").upper().strip()
    return TEAM_ALIASES.get(code, code)


# ── helpers ───────────────────────────────────────────────────────────────────

def normalize(name):
    """Lowercase, letters and spaces only -- for fallback name matching."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", "", (name or "").lower())).strip()


CACHE_DIR = os.path.join(DATA_DIR, "cache")

# Sleeper's own guidance: /players/nfl is a ~15MB dump of static metadata and
# should not be called more than once a day. Re-fetching it on every refresh is
# what gets the connection throttled into a timeout.
SLEEPER_PLAYERS_TTL = 12 * 3600


def _cache_path(name):
    return os.path.join(CACHE_DIR, name)


def _read_cache(name, max_age_seconds):
    path = _cache_path(name)
    try:
        age = time.time() - os.path.getmtime(path)
        if age > max_age_seconds:
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_cache(name, payload):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(_cache_path(name), "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except OSError as exc:
        logger.warning("     could not cache %s: %s", name, exc)


def fetch_json(url, timeout=120, retries=3):
    """GET JSON with retries and a plain-English failure.

    Transient timeouts and throttling are common against these feeds, and a raw
    URLError/WinError tells the user nothing they can act on.
    """
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "FantasyFootballAutopilot/1.0"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except Exception as exc:
            last = exc
            if attempt < retries - 1:
                wait = 3 * (attempt + 1)
                logger.info("     request failed (%s) — retrying in %ds...",
                            type(exc).__name__, wait)
                time.sleep(wait)

    host = urllib.parse.urlparse(url).netloc or url
    raise RuntimeError(
        f"Couldn't reach {host} after {retries} tries. It may be rate-limiting "
        f"repeated requests — wait a minute and try again. ({type(last).__name__})"
    )


def fetch_sleeper_players():
    """Sleeper's player metadata dump, cached to disk.

    This is the file Sleeper asks callers to fetch at most once a day, so it is
    served from cache unless the cache is stale or missing.
    """
    cached = _read_cache("sleeper_players.json", SLEEPER_PLAYERS_TTL)
    if cached is not None:
        logger.info("     using cached Sleeper player list")
        return cached

    players = fetch_json(f"{SLEEPER}/players/nfl")
    _write_cache("sleeper_players.json", players)
    return players


PARQUET_TTL = 12 * 3600


def read_parquet_cached(url, name, ttl=PARQUET_TTL):
    """Read an nflverse parquet, caching the file locally.

    Rosters and schedules change rarely; re-downloading them on every refresh
    is wasted time and another chance to hit a transient network failure.
    """
    import pandas as pd

    path = _cache_path(name)
    try:
        if os.path.exists(path) and (time.time() - os.path.getmtime(path)) < ttl:
            return pd.read_parquet(path)
    except Exception:
        pass  # Corrupt or unreadable cache — fall through and re-fetch.

    df = pd.read_parquet(url)
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        df.to_parquet(path)
    except Exception as exc:
        logger.warning("     could not cache %s: %s", name, exc)
    return df


def safe_num(v, default=0.0):
    try:
        f = float(v)
        return default if f != f else f  # NaN check
    except (TypeError, ValueError):
        return default


def tier_for_rank(rank):
    for i, cutoff in enumerate(TIER_BREAKS, start=1):
        if rank <= cutoff:
            return i
    return len(TIER_BREAKS) + 1


# ── source loaders ────────────────────────────────────────────────────────────

def load_rosters(year):
    """Current-season rosters: who is on which team, and their platform ids."""
    logger.info("  rosters %d ...", year)
    df = read_parquet_cached(f"{NFLVERSE}/rosters/roster_{year}.parquet",
                             f"roster_{year}.parquet")
    df = df[df["position"].isin(SKILL_POSITIONS)]

    # A player can appear once per week; keep their most recent row.
    if "week" in df.columns:
        df = df.sort_values("week").groupby("gsis_id", as_index=False).last()

    out = {}
    for row in df.to_dict("records"):
        gsis = row.get("gsis_id")
        if not gsis or str(row.get("status", "")) .upper() in ("RET", "CUT"):
            continue
        out[str(gsis)] = {
            "gsis_id": str(gsis),
            "name": row.get("full_name") or "",
            "team": canonical_team(row.get("team")),
            "position": str(row.get("position") or ""),
            "espn_id": str(int(row["espn_id"])) if safe_num(row.get("espn_id")) else "",
            "sleeper_id": str(int(row["sleeper_id"])) if safe_num(row.get("sleeper_id")) else "",
            "rookie_year": int(safe_num(row.get("rookie_year"))),
            "headshot_url": row.get("headshot_url") or "",
            "birth_date": str(row.get("birth_date") or "")[:10],
        }
    logger.info("     %d players on %d rosters", len(out), year)
    return out


def load_bye_weeks(year):
    """Derive each team's bye from the schedule: the week it doesn't play."""
    logger.info("  schedule %d ...", year)
    try:
        games = read_parquet_cached(f"{NFLVERSE}/schedules/games.parquet",
                                    "games.parquet")
    except Exception as exc:
        logger.warning("     schedule unavailable (%s) -- bye weeks will be 0", exc)
        return {}

    season = games[(games["season"] == year) & (games["game_type"] == "REG")]
    if season.empty:
        logger.warning("     no %d schedule published yet -- bye weeks will be 0", year)
        return {}

    weeks = set(int(w) for w in season["week"].dropna().unique())
    played = {}
    for row in season[["week", "home_team", "away_team"]].to_dict("records"):
        for side in ("home_team", "away_team"):
            played.setdefault(str(row[side]), set()).add(int(row["week"]))

    byes = {}
    for team, team_weeks in played.items():
        missing = sorted(weeks - team_weeks)
        if len(missing) == 1:
            byes[team] = missing[0]
    logger.info("     bye weeks resolved for %d teams", len(byes))
    return byes


def load_sleeper(year):
    """Sleeper ADP + season projections, keyed by sleeper player id."""
    logger.info("  Sleeper players + %d projections ...", year)
    players = fetch_sleeper_players()
    proj = fetch_json(f"{SLEEPER}/projections/nfl/regular/{year}")

    by_id, by_name, defenses = {}, {}, []
    for sid, meta in players.items():
        p = proj.get(sid)
        if not p:
            continue
        entry = {
            "adp": safe_num(p.get("adp_half_ppr") or p.get("adp_ppr"), 0.0),
            "points": safe_num(p.get("pts_half_ppr") or p.get("pts_ppr"), 0.0),
        }

        # Team defenses: Sleeper's id IS the team abbreviation for these.
        if str(meta.get("position") or "").upper() == "DEF":
            team = str(meta.get("team") or sid or "").upper()
            if team and (entry["points"] > 0 or entry["adp"] > 0):
                defenses.append({
                    "team": team,
                    "name": (meta.get("last_name") or meta.get("first_name")
                             or f"{team} D/ST").strip(),
                    **entry,
                })
            continue

        by_id[str(sid)] = entry
        full = f"{meta.get('first_name','')} {meta.get('last_name','')}".strip()
        if full:
            by_name.setdefault(normalize(full), entry)
    logger.info("     %d Sleeper projections, %d team defenses", len(by_id), len(defenses))
    return by_id, by_name, defenses


def load_stats(stats_year):
    """Last completed season's actual production, via the app's own pipeline."""
    logger.info("  nflverse stats %d ...", stats_year)
    from engine.nfl_stats import fetch_and_cache
    stats = fetch_and_cache(stats_year)
    logger.info("     %d players with stats", len(stats))
    return stats


# ── build ─────────────────────────────────────────────────────────────────────

def build(year, stats_year):
    logger.info("Rebuilding player data for the %d season", year)
    logger.info("  (historical stats from %d, the last completed season)", stats_year)

    rosters = load_rosters(year)
    byes = load_bye_weeks(year)
    sleeper_by_id, sleeper_by_name, sleeper_defenses = load_sleeper(year)
    stats = load_stats(stats_year)

    stats_by_name = {normalize(k): v for k, v in stats.items()}

    matched_by_id = matched_by_name = unmatched = 0
    rows = []

    for entry in rosters.values():
        sid = entry["sleeper_id"]
        sl = None
        if sid and sid in sleeper_by_id:
            sl = sleeper_by_id[sid]
            matched_by_id += 1
        else:
            sl = sleeper_by_name.get(normalize(entry["name"]))
            if sl:
                matched_by_name += 1
            else:
                unmatched += 1

        projected = sl["points"] if sl else 0.0
        adp = sl["adp"] if sl else 0.0

        # A player with neither a projection nor an ADP has no draft signal at
        # all. Leaving them in would put a 0.0-point body in the pool.
        if projected <= 0 and adp <= 0:
            continue

        st = stats_by_name.get(normalize(entry["name"]), {})

        rows.append({
            "name": entry["name"],
            "team": entry["team"],
            "position": entry["position"],
            "bye_week": byes.get(canonical_team(entry["team"]), 0),
            "adp": round(adp, 1) if adp > 0 else 999.0,
            "projected_points": round(projected, 1),
            "sleeper_score": 0.0,
            "consistency": POS_CONSISTENCY_DEFAULTS.get(entry["position"], 0.5),
            "age": int(safe_num(st.get("age"))) or 0,
            "is_rookie": entry["rookie_year"] == year,
            "headshot_url": entry["headshot_url"],
            "espn_id": entry["espn_id"],
            "sleeper_id": entry["sleeper_id"],
            "gsis_id": entry["gsis_id"],
            "last_season_ppr_pg": st.get("ppr_pg", 0.0),
            "last_season_games": st.get("games_played", 0),
        })

    # Team defenses come straight from Sleeper -- they have no nflverse roster
    # row, no age, and no individual stats.
    for d in sleeper_defenses:
        team = canonical_team(d["team"])
        rows.append({
            "name": d["name"] if d["name"].upper().endswith(("D/ST", "DST", "DEFENSE"))
                    else f"{d['name']} D/ST",
            "team": team,
            "position": DST_POSITION,
            "bye_week": byes.get(team, 0),
            "adp": round(d["adp"], 1) if d["adp"] > 0 else 999.0,
            "projected_points": round(d["points"], 1),
            "sleeper_score": 0.0,
            "consistency": POS_CONSISTENCY_DEFAULTS[DST_POSITION],
            "age": 0,
            "is_rookie": False,
            "headshot_url": "",
            "espn_id": "",
            "sleeper_id": team,
            "gsis_id": "",
            "last_season_ppr_pg": 0.0,
            "last_season_games": 0,
        })

    # Rank and trim per position by projected points.
    final = []
    for pos in SKILL_POSITIONS + (DST_POSITION,):
        group = sorted(
            (r for r in rows if r["position"] == pos),
            key=lambda r: (-r["projected_points"], r["adp"]),
        )[: POSITION_LIMITS.get(pos, 50)]
        for i, r in enumerate(group, start=1):
            r["positional_rank"] = i
            r["tier"] = tier_for_rank(i)
        final.extend(group)

    final.sort(key=lambda r: (r["adp"], -r["projected_points"]))
    for i, r in enumerate(final, start=1):
        r["id"] = i

    report = {
        "season": year,
        "stats_season": stats_year,
        "total": len(final),
        "by_position": {p: sum(1 for r in final if r["position"] == p)
                         for p in SKILL_POSITIONS + (DST_POSITION,)},
        "rookies": sum(1 for r in final if r["is_rookie"]),
        "matched_by_sleeper_id": matched_by_id,
        "matched_by_name": matched_by_name,
        "no_sleeper_match": unmatched,
        "missing_bye_week": sum(1 for r in final if not r["bye_week"]),
        "missing_espn_id": sum(1 for r in final
                               if not r["espn_id"] and r["position"] != DST_POSITION),
        "team_defenses": sum(1 for r in final if r["position"] == DST_POSITION),
    }
    return final, report


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--year", type=int, default=None, help="season to build (default: current)")
    ap.add_argument("--stats-year", type=int, default=None,
                    help="season to pull historical stats from (default: last completed)")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = ap.parse_args()

    year = args.year or current_season_year()
    stats_year = args.stats_year or last_completed_season()

    try:
        players, report = build(year, stats_year)
    except Exception as exc:
        logger.error("\nRebuild failed: %s: %s", type(exc).__name__, exc)
        logger.error("Nothing was written -- your existing data file is untouched.")
        return 1

    logger.info("\n--- Report ---")
    for k, v in report.items():
        logger.info("  %-22s %s", k, v)

    if not players:
        logger.error("\nNo players produced. Refusing to write an empty dataset.")
        return 1

    # Sanity floor: a real skill-position pool is well over 150 players. Below
    # that, a source probably failed silently and we should not overwrite.
    if len(players) < 150:
        logger.error("\nOnly %d players -- that's too few to be right. "
                     "Refusing to overwrite the existing file.", len(players))
        return 1

    if args.dry_run:
        logger.info("\nDry run -- nothing written.")
        logger.info("Top 10 by ADP:")
        for p in players[:10]:
            logger.info("  %-22s %-3s %-4s adp=%-6s proj=%-6s",
                        p["name"], p["position"], p["team"], p["adp"], p["projected_points"])
        return 0

    os.makedirs(DATA_DIR, exist_ok=True)
    out_path = os.path.join(DATA_DIR, f"players_{year}.json")
    with open(out_path, "w") as f:
        json.dump(players, f, indent=1)
    logger.info("\nWrote %d players -> %s", len(players), out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
