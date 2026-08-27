@echo off
setlocal
title Update Player Data

REM ===========================================================
REM  Update Player Data
REM
REM  Rebuilds the player dataset for the current season from
REM  live sources: nflverse (rosters, stats, schedule) and
REM  Sleeper (ADP, projections).
REM
REM  Takes a few minutes -- it downloads real data.
REM ===========================================================

cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo.
echo  ==========================================
echo   UPDATE PLAYER DATA
echo  ==========================================
echo.
echo  Pulling live data from nflverse and Sleeper.
echo  This takes a few minutes. Nothing is overwritten
echo  unless the rebuild succeeds.
echo.

"%PY%" rebuild_players.py %*
if errorlevel 1 (
    echo.
    echo  [X] Rebuild failed. Your existing player data is unchanged.
    echo.
    echo      If it says a source could not be reached, check your
    echo      internet connection and try again.
    echo.
    pause
    exit /b 1
)

echo.
echo  [ok] Player data updated. Restart Autopilot to load it.
echo.
pause
