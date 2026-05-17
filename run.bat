@echo off
python pipeline.py
if errorlevel 1 (
    echo Pipeline failed.
    pause
    exit /b 1
)
start events.html
