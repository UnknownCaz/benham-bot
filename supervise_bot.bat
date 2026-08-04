@echo off
REM ---------------------------------------------------------------------------
REM benham-bot supervisor - entry point. Keeps exactly ONE benham.bot alive,
REM restarting it when it exits, and giving up if it cannot start at all.
REM
REM This is a shim. The logic lives in supervise_bot.ps1, because the two things
REM that make a supervisor worth running - deciding whether an exit was a crash
REM loop, and rotating its own log - both need arithmetic that batch does badly.
REM This file stays as the entry point so the Scheduled Task and the README have
REM one stable thing to point at.
REM
REM WARNING: never run a second copy while one is running - two processes share
REM the same bot token = double gateway = duplicate replies and duplicate outbox
REM actions. The script refuses to start in that case rather than trusting this
REM comment. Output is appended to supervise.log (rotated at 5MB).
REM ---------------------------------------------------------------------------
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0supervise_bot.ps1" %*
