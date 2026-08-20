@echo off
REM initiate.bat - the pinned launcher for the initiative lane.
REM
REM Two lessons from the conversation-history-sync task are baked in here, and
REM both of them are why this file exists rather than the scheduled job just
REM running `python benham.py initiate ...`:
REM
REM   A COMMAND WHOSE TEXT VARIES PER RUN CANNOT BE PRE-APPROVED. Tool approvals
REM   are stored as literal strings, so `cd <repo> && python benham.py ...` can
REM   never be covered by one rule - and a compound `cd X && y` cannot be covered
REM   by a prefix rule at all. This script owns the cd, so every invocation is
REM   the same fixed path plus arguments, which one prefix rule does cover.
REM   Without that, an unattended run stalls on a permission prompt nobody can
REM   answer, which is exactly how the last scheduled task on this machine died.
REM
REM   THIS MACHINE HAS TWO PYTHONS ON PATH. The real one, and the package-less
REM   Microsoft Store stub. Bare `python` resolves differently depending on which
REM   shell is asking, so the interpreter is pinned below rather than inherited.
REM
REM Usage is identical to `python benham.py initiate ...`:
REM   initiate.bat status
REM   initiate.bat ask "..." --dry-run

setlocal
set "REPO=%~dp0.."
set "PY=C:\Users\Tyler\AppData\Local\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"
cd /d "%REPO%" || exit /b 1
"%PY%" benham.py initiate %*
exit /b %ERRORLEVEL%
