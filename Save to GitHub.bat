@echo off
setlocal enabledelayedexpansion
title Save to GitHub

REM ===========================================================
REM  Save to GitHub
REM
REM  Double-click at the end of a session. Shows what changed,
REM  commits everything, and pushes to the remote.
REM
REM  Put this file INSIDE the repo folder, next to PLAN.md.
REM ===========================================================

cd /d "%~dp0"

if not exist ".git" (
    echo.
    echo  [X] This isn't a git repository.
    echo      Put "Save to GitHub.bat" inside the project folder --
    echo      the one containing PLAN.md and the engine folder.
    echo.
    pause
    exit /b 1
)

where git >nul 2>&1
if errorlevel 1 (
    echo  [X] Git isn't on your PATH. Install from https://git-scm.com/download/win
    pause
    exit /b 1
)

for /f "tokens=*" %%b in ('git rev-parse --abbrev-ref HEAD 2^>nul') do set "BRANCH=%%b"

echo.
echo  ==========================================
echo   SAVE TO GITHUB   (branch: !BRANCH!)
echo  ==========================================
echo.

REM ---------- anything to do? ----------
git diff --quiet && git diff --cached --quiet
if not errorlevel 1 (
    REM Working tree clean -- but there may still be unpushed commits.
    git status -sb | findstr /C:"ahead" >nul
    if errorlevel 1 (
        echo  [ok] Nothing to save. Everything is already committed and pushed.
        echo.
        pause
        exit /b 0
    )
    echo  [..] No new file changes, but you have unpushed commits. Pushing...
    goto :push
)

REM ---------- show what changed ----------
echo  Files changed:
echo.
git -c color.ui=false status --short
echo.

REM ---------- safety: never commit the ESPN session ----------
git status --porcelain | findstr /C:"espn_config.json" >nul
if not errorlevel 1 (
    echo  [X] STOP -- espn_config.json is about to be committed.
    echo.
    echo      That file holds your ESPN login session and must never go
    echo      to a public repo. It should be ignored by .gitignore.
    echo      Fix that first, then run this again.
    echo.
    pause
    exit /b 1
)

REM ---------- commit message ----------
echo.
set "MSG="
set /p "MSG=Describe what changed (or press Enter for a dated message): "
if "!MSG!"=="" (
    for /f "tokens=*" %%d in ('powershell -NoProfile -Command "Get-Date -Format \"yyyy-MM-dd HH:mm\""') do set "STAMP=%%d"
    set "MSG=Session changes !STAMP!"
)

echo.
echo  [..] Committing: !MSG!
git add -A
git commit -m "!MSG!"
if errorlevel 1 (
    echo  [X] Commit failed. Nothing was pushed.
    pause
    exit /b 1
)

:push
echo.
echo  [..] Pushing to GitHub...
git push
if errorlevel 1 (
    echo.
    echo  [!] Push failed. Common causes:
    echo        - No upstream set for this branch. Try:
    echo             git push -u origin !BRANCH!
    echo        - Someone else pushed first. Try:  git pull --rebase
    echo        - Not signed in to git. Try:       git push   (and follow the prompt)
    echo.
    echo      Your work IS committed locally either way -- nothing is lost.
    echo.
    pause
    exit /b 1
)

echo.
echo  [ok] Saved to GitHub.
echo.
git -c color.ui=false log --oneline -3
echo.
pause
