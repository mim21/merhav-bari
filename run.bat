@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo ── Step 0: Extracting events from chat ──
claude -p "Read the WhatsApp chat file and update events.json with all upcoming events. Follow CLAUDE.md extraction rules exactly. Check each registration website."
if errorlevel 1 echo Warning: extraction reported an error. Continuing with existing events.json...
echo.

echo ── Steps 1-4: Running pipeline ──
python pipeline.py
if errorlevel 1 (
    echo Pipeline failed.
    pause
    exit /b 1
)

start index.html

echo ── Step 5: Publishing to GitHub Pages ──
git -C "%~dp0" add -f index.html calendar.ics events.json
git -C "%~dp0" commit -m "Update events" 2>nul
git -C "%~dp0" push
echo Done. Live at https://mim21.github.io/merhav-bari/
