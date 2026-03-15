# Fantasy Football Autopilot — Plan

> **Agents: Read this file before starting any work. Cross off tasks as you complete them.**
> Mark done items with ✅ or ~~strikethrough~~.

---

## Phase 1 — Core App ✅
- ✅ Project structure (folders, requirements.txt, config.py)
- ✅ Player dataset (`data/players_2025.json`) — 254 players with projections, ADP, tiers, sleeper scores
- ✅ Draft engine (`engine/draft.py`) — DraftState, snake draft logic, pick/undo
- ✅ Rankings engine (`engine/rankings.py`) — VBD scoring, best available
- ✅ Recommendations (`engine/recommendations.py`) — best overall, by need, sleepers
- ✅ Scarcity analysis (`engine/scarcity.py`) — positional depletion tracking
- ✅ Win probability (`engine/win_probability.py`) — roster strength vs league average
- ✅ Flask app (`app.py`) — all REST API routes
- ✅ Frontend HTML (`templates/index.html`) — single-page layout
- ✅ CSS (`static/css/style.css`) — dark theme, position color coding
- ✅ JavaScript (`static/js/app.js`) — draft board, available list, recommendations, scarcity chart

---

## Phase 2 — ESPN Live Sync

- ✅ Install `espn-api` and `requests` packages
- ✅ Save ESPN credentials to `espn_config.json` (SWID + espn_s2 + league_id: 1273679521)
- ✅ Build `engine/espn_sync.py` — polling, pick sync, player name matching
- ✅ Add ESPN API routes to `app.py` (`/api/espn/connect`, `/api/espn/start`, `/api/espn/stop`, `/api/espn/status`)
- ✅ Add ESPN sync badge and modal to UI
- ✅ Fix ESPN API base URL → `lm-api-reads.fantasy.espn.com`
- ✅ Fix team sort lambda bug in `connect()`
- ✅ Fix team owner parsing (owners are strings, not objects)
- ✅ Confirmed: Connected to league **GAODT** — 10 teams, 15 rounds, snake, user is pick #8 (index 7)

### Remaining ESPN Tasks
- ✅ Fix empty team names — fallback chain: location+nickname → abbrev → "Team N"
- ✅ Auto-configure draft settings from ESPN league info (set `num_teams=10`, `user_team_index=7`, `num_rounds=15` automatically on connect)
- [ ] Test live pick sync during an active draft — verify player name matching works end-to-end
- ✅ Handle unknown ESPN players — removed bad VBD fallback, now skips unresolvable picks safely
- ✅ Show ESPN team names on draft board and "On the Clock" (injected via `/api/state`)
- ✅ Show "Draft already complete" notice — yellow warning banner in ESPN modal

---

## Phase 3 — Polish & Accuracy

- [ ] Improve player matching accuracy — add ESPN player IDs to `players_2025.json` so sync doesn't rely on name fuzzy matching
- ✅ Update player projections/ADP for 2025 season accuracy — corrected 8 key players (Saquon, Lamar, Jayden Daniels, Josh Allen, Joe Burrow, Derrick Henry, Anthony Richardson, Drake Maye)
- ✅ Replace static sleeper scores with dynamic model — computed from ADP rank vs VBD rank per position, requires positive VBD to qualify
- ✅ Add draft timer display (countdown per pick) — configurable seconds-per-pick in Settings, turns yellow <20s, red+blink <10s
- ✅ Add "auto-pick CPU teams" simulation mode — "Sim CPU" button in header, fast-forwards until user's pick
- ✅ Export user roster to clipboard/CSV after draft — "Export" button in My Team panel, copies CSV to clipboard
- ✅ Make UI responsive for smaller screens / dual-monitor setups — breakpoints at 1300px, 1100px, 1000px, 768px

---

## Phase 4 — Desktop App (flaskwebgui)

Note: pywebview requires pythonnet which has no Python 3.14 wheel yet. Using flaskwebgui instead — opens Edge/Chrome in app mode (no URL bar, standalone window). Same UX result.

- ✅ Add `flaskwebgui` + `pyinstaller` to `requirements.txt`
- ✅ Create `desktop.py` — starts Flask via FlaskUI, opens Edge/Chrome in app mode
- ✅ Create `build.spec` — PyInstaller config bundling templates/, static/, data/, engine/
- ✅ Create `runtime_hook.py` — fixes asset paths when running from frozen .exe
- ✅ Package to single folder with PyInstaller: `python -m PyInstaller build.spec -y`
- ✅ Test packaged `.exe` — all routes 200, favicon loads, espn_config.json auto-created next to exe

---

---

## Performance Optimizations ✅

- ✅ O(1) `picks_until_user_turn()` — replaced O(n) loop with modular arithmetic
- ✅ `/api/dashboard` consolidated endpoint — single HTTP round-trip replaces 4 (state + recs + scarcity + win-prob)
- ✅ ESPN sync: only triggers `fetchAvailable`+`refreshAll` when `known_picks` count changes (not every 5s unconditionally)
- ✅ ESPN `_fetch_picks`: merged `kona_player_info` view into single ESPN API call — eliminates second ESPN round-trip per new-pick cycle
- ✅ `need_multiplier`: `total_starters` pre-computed once per `get_recommendations` call instead of once per player
- ✅ `updateTimerDisplay`: caches `#timer-value` DOM element (avoids `getElementById` on every 1s tick)
- ✅ `renderDraftBoard`: skips re-render when pick count unchanged (`lastRenderedPickCount` guard)

---

## Phase 5 — CPU Strategy & Sleeper Integration ✅

- ✅ `engine/cpu_strategy.py` — three-signal scoring: VBD × positional need × ADP urgency; softmax-weighted pick from top 8 candidates
- ✅ `/api/simulate` — updated to use `select_cpu_pick` instead of always picking top VBD player
- ✅ `engine/sleeper_sync.py` — fetches fresh ADP + projected points from Sleeper API (no auth); patches player data in-place and recomputes VBD
- ✅ `/api/sleeper/refresh` endpoint — triggers Sleeper data pull, returns updated/total counts
- ✅ Settings modal — "↻ Refresh Player Data" button with status feedback

---

## Phase 6 — File / Launch Cleanup ✅

- ✅ Renamed `app.py` → `AutoPicker_Browser.py`
- ✅ Renamed `desktop.py` → `AutoPicker_Desktop.py` (updated import)
- ✅ Created `Fantasy Auto Picker.bat` — double-click launcher
- ✅ Updated `build.spec` to reference `AutoPicker_Desktop.py`

---

## Phase 7 — Draft Type Expansion

Architecture: add `draft_type` field to `DraftState` and branch type-specific behavior in-place (strategy pattern — no separate subclasses).

### Phase A — Foundation (prerequisite for B/C/D) ✅
- ✅ Add `draft_type` to `config.py` (`"snake"`, `"auction"`, `"best_ball"`, `"dynasty"`)
- ✅ Add `draft_type` to `DraftState.__init__` and `get_state_dict()`
- ✅ Update `/api/configure` to accept and store `draft_type`
- ✅ Replace snake checkbox in Settings modal with draft type `<select>` (snake toggle row hidden for auction)
- ✅ Frontend: draft type badge in header; `onDraftTypeChange()` shows/hides snake toggle

### Phase B — Best Ball ✅
- ✅ `recommendations.py`: compressed `need_multiplier` range for best ball (`1.15/1.0/0.85` vs `1.5/1.0/0.3`); `get_recommendations` and `_score_by_need` accept `best_ball` flag
- ✅ Roster panel: best ball shows all players sorted by projected_points with "Auto-optimized lineup weekly" note — no empty slot layout
- ✅ Draft type badge already handles best ball green color (Phase A)

### Phase C — Dynasty / Keeper ✅
- ✅ `draft.py`: `load_keepers()` pre-fills team rosters, removes keepers from available pool, marks `is_keeper: True`; `is_rookie_only_round` property; `get_available_players()` filters to rookies in rookie-only rounds; `get_state_dict()` includes `keeper_player_ids`, `is_rookie_only_round`, `keeper_slots`
- ✅ `config.py`: `dynasty.keeper_slots`, `dynasty.rookie_only_rounds`, `dynasty.keepers` defaults added
- ✅ Added `/api/keepers` GET + POST endpoints (reset + reload keepers, guard against post-draft changes)
- ✅ `cpu_strategy.py`: no changes needed — `get_available_players()` already returns rookies-only in rookie rounds
- ✅ UI: keeper assignment modal (searchable player list per team, cross-team exclusion, save/close); "Set Keepers" button appears in settings when dynasty selected; dynasty row for keeper slots input
- ✅ Draft board: keeper row (K) shown above round rows in dynasty mode, with purple-tinted cells and position badges; `_availablePlayers` cached for keeper modal
- [ ] `players_2025.json`: add `age: int` and `is_rookie: bool` fields (deferred — requires external data source)
- [ ] ESPN sync: auto-import keepers if connected (deferred — requires 2026 season)

### Phase D — Auction / Salary Cap ✅
- ✅ `draft.py`: `team_budgets`, `current_nomination_team`, `active_auction`, `auction_results`; `start_nomination()`, `place_bid()`, `pass_bid()`, `close_auction()`, `can_afford()`, `_next_nomination_team()`, `is_user_nomination_turn`; `draft_complete` handles auction roster-full condition; `get_state_dict()` includes full auction state
- ✅ `config.py`: `auction.budget_per_team` ($200), `auction.min_bid` ($1), `auction.nomination_order` ("snake") sub-config added
- ✅ `cpu_strategy.py`: `select_cpu_nomination()` (self-interested or disruptive 50/50), `select_cpu_bid()` (fair-value × need × variance ceiling), `_estimate_auction_value()` (VBD-share × remaining league budget)
- ✅ `recommendations.py`: `_auction_recommendations()` — best value, best by need, bargain targets (surplus vs ADP-implied price), active auction bid guidance (est value, max bid, verdict: good deal / fair / overpay)
- ✅ New endpoints: `POST /api/auction/nominate`, `POST /api/auction/bid`, `POST /api/auction/pass`, `_simulate_auction_step()`, `_run_cpu_bids()` helper
- ✅ Updated `/api/simulate`: branches on `draft_type == "auction"` to run CPU nomination + CPU bid loop
- ✅ `espn_sync.py`: `_espn_draft_type()` maps `"AUCTION"/"SALARY_CAP"` → `"auction"`; `/api/espn/connect` passes `draft_type` into config
- ✅ UI: auction bid panel (nominate mode, bid mode, waiting mode); auction header (budget, on the block, current bid, high bidder); auction draft board (team columns with price tags); recommendation labels change to "Best Value / Best by Need / Bargain Targets"; Available list shows "Nominate" button in auction mode
- [ ] State persistence (JSON save/load) — deferred; auction session is browser-tab-scoped for now

### Key Decisions
- Auction timing: **turn-based** for local sim (not real-time countdown); real-time only if syncing ESPN auction
- CPU bid pacing: instant resolve with optional visual delay
- Keeper source: manual UI + auto-import if ESPN connected
- State persistence: add JSON save/load before building auction mode

---

## Phase 8 — Scoring Model Improvements ✅

- ✅ Replaced step-function need multiplier with smooth linear curve (1.5→0.3 as slots fill)
- ✅ Added tier-break detection — find natural VBD cliffs per position, boost urgency when few players remain above a drop-off (1.0–1.5 range)
- ✅ Added survival probability — estimate P(player available at next pick) using ADP runway; urgency multiplier 1.0–1.5
- ✅ Added consistency factor — starters prefer consistent players, bench prefers ceiling (0.85–1.15 range)
- ✅ Added `consistency` field to all 254 players in `players_2025.json` (42 hand-tuned, 212 position defaults)
- ✅ Added consistency fallback in `rankings.py` `load_players()` for any players missing the field
- ✅ CPU strategy (`cpu_strategy.py`) rewritten to use same 4-factor model: `vbd × need × tier_urgency × survival_urgency × consistency`
- ✅ Fixed CPU double-negative bug where negative VBD × negative ADP factor produced false high scores (all CPUs were drafting backup QBs)

---

## Phase 9 — Data-Driven Multi-Dimensional Scoring (nflverse) ✅

- ✅ `engine/nfl_stats.py` — fetches weekly player stats, snap counts, and roster data from `nfl_data_py` (nflverse); caches to `data/cache/`; converts pandas → plain dicts at boundary
- ✅ `engine/stat_processor.py` — computes 7 component scores per player (volume, efficiency, td_role, team_env, consistency, ceiling, risk); percentile-ranked within position; position-specific weights; scoring format support (PPR/Half/Standard); scales to projected-points range
- ✅ `rankings.py` updated: `_player_value()` uses `base_value` when available, falls back to `projected_points` (K, DST, rookies without stats)
- ✅ `POST /api/stats/refresh` — fetches NFL stats, processes components, patches 190/254 players with base_value + component scores; recomputes VBD
- ✅ `GET /api/stats/components/<player_id>` — returns component breakdown for UI display
- ✅ Name matching with suffix stripping (Jr., III, etc.) for nflverse → local player matching
- ✅ UI: "Refresh NFL Stats" button in Settings modal; component mini-bars (volume/efficiency/td/ceiling) on Available Players list
- ✅ `requirements.txt` updated with `nfl_data_py`, `pandas`, `pyarrow`
- [ ] UI: full component radar chart on player detail/hover (deferred)
- [ ] Red zone stats from play-by-play data (deferred — PBP is ~300MB per season)
- [ ] Year-over-year trend analysis (deferred)

---

## Phase 10 — Deferred Items Completed ✅

- ✅ **Rebuilt .exe** — `build.spec` updated with `collect_all` for `nfl_data_py`, `pandas`, `pyarrow`; all new engine modules bundled; verified in `dist/`
- ✅ **Radar chart UI** — SVG spider chart (7 axes: volume/efficiency/td/team/consistency/ceiling/safety) in player detail modal; clicking a player row opens the modal; Draft button still drafts directly; no external libraries
- ✅ **`is_rookie`/`age` fields** — Patched all 254 players in `players_2025.json` from nflverse 2025 seasonal rosters; 208/254 matched; `is_rookie` and `age` now present; dynasty rookie-only round filter now works correctly
- ✅ **ESPN sync test** — Added suffix-stripping to `_resolve_player` (fixes "Patrick Mahomes II" not resolving to "Patrick Mahomes"); comprehensive unit tests cover: name normalization, suffix stripping, ESPN ID caching, draft type mapping, team map building, user index detection

## Phase 12 — Module-by-Module Optimization ✅

- ✅ `rankings.py` — error handling on `load_players`, `_player_value()` helper, single position grouping passed to VBD + sleeper
- ✅ `recommendations.py` — extracted `_starter_need_for_position()`, per-position caching, `_compute_draft_context()`, `_total_starters()`, `_fair_value()` helpers
- ✅ `cpu_strategy.py` — shared `_fair_value`/`_total_starters` from recommendations, per-position caching in `_score_candidates`
- ✅ `nfl_stats.py` — `to_dict("records")` replaces `iterrows()`, single-pass aggregation, merged team totals into player loop
- ✅ `stat_processor.py` — bisect percentile ranking O(n log n), dispatch dict, sum-of-squares variance
- ✅ `draft.py` — consolidated auction init, cached `_all_teams_set` frozenset, optimized `get_positional_counts`
- ✅ `AutoPicker_Browser.py` — module-level name helpers (no re-import per request), loop-based key copy, reuse `_all_teams_set`
- ✅ `scarcity.py`, `win_probability.py`, `sleeper_sync.py`, `espn_sync.py` — reviewed, already clean

---

## Known Issues / Notes

- ESPN API requires `lm-api-reads.fantasy.espn.com` subdomain (not `fantasy.espn.com`)
- `espn_config.json` is gitignored — never commit it
- The 2025 draft for league GAODT is **already complete** (`drafted: true, inProgress: false`) — live sync testing must wait for next draft or a test league
- Player name matching falls back to top-available-by-VBD if ESPN player name isn't in our dataset — this can cause wrong players to be recorded during sync
