# Fantasy Football Autopilot — Project Guidelines

## For All Agents

**Before starting any work, read PLAN.md first.**
- Check which tasks are complete (marked with ~~strikethrough~~ or ✅) and which are still pending
- Do not redo completed work
- When you finish a task, update PLAN.md: cross out the item with ~~strikethrough~~ or mark it ✅

## Project Overview

A local web app (Flask + vanilla JS) that acts as a real-time AI draft assistant during ESPN Fantasy Football drafts.

- Backend: Python/Flask (`app.py`)
- Engine modules: `engine/` (draft.py, rankings.py, recommendations.py, scarcity.py, win_probability.py, espn_sync.py)
- Frontend: single-page app in `templates/index.html` + `static/`
- Player data: `data/players_2025.json` (~254 players)
- ESPN credentials: `espn_config.json` (never commit this file — it's in .gitignore)

## Running the App

```bash
python app.py
```
Then open http://localhost:5000

## Key Conventions

- All API routes return JSON and live in `app.py`
- The global `draft` (DraftState) and `espn` (ESPNSync) objects are module-level in `app.py`
- ESPN API base URL: `https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{year}/segments/0/leagues/{league_id}`
- Player name matching is done via normalized names (lowercase, strip punctuation) in `ESPNSync._name_map`
- Never commit `espn_config.json` — it contains real credentials
