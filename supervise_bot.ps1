<#
supervise_bot.ps1 - keep exactly ONE bot.py alive, and know when to stop trying.

The plain restart loop this replaces had two problems that only show up at 3am.

  A crash loop is not a crash. If bot.py dies immediately - bad token, a syntax
  error in a file someone edited, a missing dependency - restarting it every ten
  seconds forever produces thousands of failed Discord logins and a log nobody can
  read, and it never fixes itself. Restarting is the right answer to "the process
  went away", and the wrong answer to "the process cannot start". These are told
  apart by how long it ran: an exit after a minute of healthy uptime is the first
  kind, five exits in a row inside a minute each is the second, and the second gives
  up rather than hammering.

  A supervisor that appends forever writes an unbounded file. One generation of
  rotation at 5MB, matching jsonio.rotate_if_large, which does the same thing for
  the bot's own transcripts.

The single-instance rule is the important one. Two processes on one bot token means
two gateway connections: Benham answers everything twice and every queued outbox
action fires twice. Guarded twice here - a named mutex so two supervisors cannot
overlap, and a process check so the supervisor refuses to start while a bot.py that
someone launched by hand is still running.

    powershell -NoProfile -ExecutionPolicy Bypass -File supervise_bot.ps1

Normally launched by the benham-bot logon Scheduled Task, whose action wraps this in
"conhost.exe --headless" so it runs with no console window at all. Do not "simplify" that to
powershell -WindowStyle Hidden: on Windows 11 the console gets handed off to Windows Terminal,
which ignores the hidden style, and the supervisor reappears in Alt+Tab. Nothing is lost by
running headless - every line below also goes to supervise.log.
#>
param(
    [int]$MaxLogMB = 5,           # rotate supervise.log past this
    [int]$FastExitSeconds = 60,   # ran for less than this = a "fast" exit
    [int]$MaxFastExits = 5,       # this many in a row = stop trying
    [int]$BaseDelaySeconds = 10,
    [int]$MaxDelaySeconds = 300
)

$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Dir
$Log = Join-Path $Dir 'supervise.log'

function Write-Log($msg) {
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ssZ')
    $line = "[$stamp] [supervisor] $msg"
    try { Add-Content -Path $Log -Value $line -Encoding utf8 } catch {
        # A hand-started bot.py holds supervise.log exclusively (its stdout is
        # redirected there), which is exactly the situation where the refusal
        # below most needs to be readable somewhere. Fall back to a side file
        # rather than letting the explanation vanish.
        try { Add-Content -Path "$Log.locked-out" -Value $line -Encoding utf8 } catch {}
    }
    Write-Host $line
}

function Rotate-Log {
    # One generation, replacing any previous .1 - same convention as the bot's own
    # transcripts. These are diagnostics, not an audit trail; bounding disk matters
    # more than keeping every generation.
    if (Test-Path $Log) {
        try {
            if ((Get-Item $Log).Length -gt ($MaxLogMB * 1MB)) {
                Move-Item -Force $Log "$Log.1"
                Write-Log "rotated supervise.log (was over ${MaxLogMB}MB)"
            }
        } catch {}
    }
}

function Get-BotPid {
    # Same query status.py uses, so both agree on what "the bot is running" means.
    try {
        $p = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction Stop |
             Where-Object { $_.CommandLine -match 'bot\.py' } |
             Select-Object -First 1 -ExpandProperty ProcessId
        return $p
    } catch { return $null }
}

# --- single instance ------------------------------------------------------
$mutex = New-Object System.Threading.Mutex($false, 'Local\benham-bot-supervisor')
if (-not $mutex.WaitOne(0)) {
    Write-Log "another supervisor already holds the lock - exiting rather than starting a second"
    exit 1
}

$already = Get-BotPid
if ($already) {
    Write-Log "bot.py is already running (pid $already), started outside this supervisor."
    Write-Log "Refusing to start a second - one token, two gateways means duplicate replies"
    Write-Log "and duplicate outbox actions. Stop that process first, then run this again."
    $mutex.ReleaseMutex()
    exit 1
}

# --- the loop -------------------------------------------------------------
Write-Log "supervisor up (fast-exit threshold ${FastExitSeconds}s, give up after $MaxFastExits)"
$fastExits = 0

try {
    while ($true) {
        Rotate-Log
        Write-Log "starting bot.py"
        $started = Get-Date

        # cmd does the redirect, not PowerShell. In 5.1, redirecting a native
        # command's stderr from PowerShell wraps every line in an ErrorRecord and
        # sets $? false even on a clean exit, which would make every shutdown look
        # like a failure in the log.
        & cmd /c "python -u bot.py >> `"$Log`" 2>&1"
        $code = $LASTEXITCODE
        $ran = [int]((Get-Date) - $started).TotalSeconds

        if ($ran -ge $FastExitSeconds) {
            # It was up long enough to have been working. Whatever ended it was an
            # event, not a defect, so the streak resets and the retry stays quick.
            $fastExits = 0
            $delay = $BaseDelaySeconds
        } else {
            $fastExits++
            $delay = [Math]::Min($BaseDelaySeconds * [Math]::Pow(2, $fastExits - 1), $MaxDelaySeconds)
        }
        Write-Log "bot.py exited (code $code) after ${ran}s - fast-exit streak $fastExits/$MaxFastExits"

        if ($fastExits -ge $MaxFastExits) {
            Write-Log "GIVING UP: $MaxFastExits consecutive exits, none lasting ${FastExitSeconds}s."
            Write-Log "That is a bot that cannot start rather than one that fell over - a bad"
            Write-Log "token, an import error, a missing dependency. Restarting it again will not"
            Write-Log "help. Check the bot output above this line, fix it, then start the"
            Write-Log "supervisor again (or log out and back in for the Scheduled Task)."
            break
        }

        Write-Log "restarting in $([int]$delay)s"
        Start-Sleep -Seconds ([int]$delay)
    }
} finally {
    try { $mutex.ReleaseMutex() } catch {}
    Write-Log "supervisor stopped"
}
