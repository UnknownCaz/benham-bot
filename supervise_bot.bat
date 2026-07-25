@echo off
REM ---------------------------------------------------------------------------
REM benham-bot supervisor: keeps exactly ONE bot.py instance alive, restarting
REM it if it exits or crashes. Intended to be launched by a logon Scheduled Task
REM (see README "24/7 supervision"), so the bot survives reboots and crashes.
REM
REM WARNING: never run a second copy while one is running - two processes share
REM the same bot token = double gateway = duplicate actions. Stop any running
REM bot.py first. Output is appended to supervise.log.
REM ---------------------------------------------------------------------------
cd /d "%~dp0"
:loop
echo [%date% %time%] starting bot.py >> supervise.log
python -u bot.py >> supervise.log 2>&1
echo [%date% %time%] bot.py exited (code %errorlevel%), restarting in 10s >> supervise.log
timeout /t 10 /nobreak > nul
goto loop
