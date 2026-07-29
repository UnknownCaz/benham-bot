<#
tray_bot.ps1 - a tray icon for Benham: is it up, and the few things worth doing to it.

Deliberately NOT the supervisor. supervise_bot.ps1 keeps running headless under the
benham-bot Scheduled Task, and this watches it. Folding supervision into a UI process
would mean a bug in a menu handler, or Tyler closing the icon, takes the bot down with
it - and "the thing that restarts the bot" should be the least interesting process on
the machine, not the one drawing pictures.

So this owns no state. It polls, it renders, and its menu items are the same actions
that are already available from a shell. Closing it changes nothing.

  grey   supervisor not running - nothing will restart the bot
  green  supervisor running, bot up
  red    supervisor running, bot down (a restart is presumably in flight)

ONE ASYMMETRY WORTH KNOWING. The guest menu can turn guest chat OFF and cannot turn it
ON. Off is the fail-safe direction and worth having a panic button for; on is widening
who may talk to Benham, and that stays a deliberate edit to control.json - a decision
that should cost more than a right-click, and should not have a second path into it
sitting in a tray menu.

    powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File tray_bot.ps1
#>

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Dir
$Log = Join-Path $Dir 'supervise.log'
$ControlFile = Join-Path $Dir 'control.json'
$TaskName = 'benham-bot'

# --- state readers (all read-only) ----------------------------------------

function Get-BotPid {
    # The same CIM query status.py and supervise_bot.ps1 use, so all three agree on
    # what "the bot is running" means.
    try {
        Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction Stop |
            Where-Object { $_.CommandLine -match 'bot\.py' } |
            Select-Object -First 1 -ExpandProperty ProcessId
    } catch { $null }
}

function Get-SupervisorUp {
    try {
        $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        return ($t.State -eq 'Running')
    } catch { return $false }
}

function Get-GuestState {
    # Reads the file rather than asking the bot, so it reflects what the NEXT start
    # will do. When they disagree the tooltip says "restart pending", which is the
    # honest answer for a config that is only read at import.
    try {
        $g = (Get-Content $ControlFile -Raw -ErrorAction Stop | ConvertFrom-Json).guest
        if ($null -eq $g) { return @{ On = $false; Count = 0 } }
        $ids = @($g.ids)
        return @{ On = [bool]$g.enabled; Count = $ids.Count }
    } catch { return @{ On = $false; Count = 0 } }
}

function Get-BotUptime($botPid) {
    try {
        $p = Get-Process -Id $botPid -ErrorAction Stop
        $s = (Get-Date) - $p.StartTime
        if ($s.TotalHours -ge 1) { return ("{0}h {1}m" -f [int]$s.TotalHours, $s.Minutes) }
        if ($s.TotalMinutes -ge 1) { return ("{0}m" -f [int]$s.TotalMinutes) }
        return ("{0}s" -f [int]$s.TotalSeconds)
    } catch { return "?" }
}

# --- icons ----------------------------------------------------------------
# Built once and reused. Building them per tick would leak a GDI handle every few
# seconds, which on a process meant to run for weeks eventually stops drawing
# anything at all.

function New-DotIcon([System.Drawing.Color]$color) {
    $bmp = New-Object System.Drawing.Bitmap 16, 16
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = 'AntiAlias'
    $g.Clear([System.Drawing.Color]::Transparent)
    $brush = New-Object System.Drawing.SolidBrush $color
    $g.FillEllipse($brush, 1, 1, 14, 14)
    $pen = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(90, 0, 0, 0)), 1
    $g.DrawEllipse($pen, 1, 1, 14, 14)
    $brush.Dispose(); $pen.Dispose(); $g.Dispose()
    $icon = [System.Drawing.Icon]::FromHandle($bmp.GetHicon())
    $bmp.Dispose()
    return $icon
}

$IconGreen = New-DotIcon ([System.Drawing.Color]::FromArgb(60, 190, 90))
$IconRed = New-DotIcon ([System.Drawing.Color]::FromArgb(215, 70, 70))
$IconGrey = New-DotIcon ([System.Drawing.Color]::FromArgb(140, 140, 140))

# --- tray -----------------------------------------------------------------

$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = $IconGrey
$notify.Text = "Benham: checking..."
$notify.Visible = $true

$menu = New-Object System.Windows.Forms.ContextMenuStrip
$notify.ContextMenuStrip = $menu

function Add-Item($text, $action) {
    $i = $menu.Items.Add($text)
    if ($action) { $i.add_Click($action) } else { $i.Enabled = $false }
    return $i
}

$miStatus = Add-Item "Benham: checking..." $null
$miGuest = Add-Item "Guest chat: ?" $null
$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null

Add-Item "Restart bot" {
    # Kill it and let the supervisor bring it back - that is the supervisor's whole
    # job, and going through it means one code path for restarts instead of two.
    # No supervisor running is not a reason to refuse: stop the bot first (so the
    # supervisor's already-running check cannot trip), then start the supervisor,
    # which starts the bot itself. Same end state either way - bot up, supervised.
    $p = Get-BotPid
    if (Get-SupervisorUp) {
        if ($p) {
            Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
            $notify.ShowBalloonTip(4000, "Benham", "Bot stopped - supervisor restarts it in ~10s.", 'Info')
        } else {
            $notify.ShowBalloonTip(4000, "Benham", "Bot was not running; supervisor should be starting it.", 'Info')
        }
        return
    }
    try {
        if ($p) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 1 }
        Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        Start-Sleep -Seconds 4
        if (Get-SupervisorUp) {
            $notify.ShowBalloonTip(4000, "Benham",
                "Supervisor started - it brings the bot up in a few seconds.", 'Info')
        } else {
            [System.Windows.Forms.MessageBox]::Show(
                "Stopped the bot but the supervisor did not stay up - check supervise.log " +
                "(and supervise.log.locked-out) for why. The bot is currently DOWN.",
                "Benham - restart incomplete", 'OK', 'Warning') | Out-Null
        }
    }
    catch { [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, "Benham", 'OK', 'Error') | Out-Null }
} | Out-Null

$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null

Add-Item "Disable guest chat + restart" {
    # Off only. See the header: turning guests ON stays a deliberate file edit.
    $g = Get-GuestState
    if (-not $g.On) {
        $notify.ShowBalloonTip(3000, "Benham", "Guest chat is already off.", 'Info')
        return
    }
    $answer = [System.Windows.Forms.MessageBox]::Show(
        "Set guest.enabled = false and restart the bot so it takes effect?",
        "Benham - disable guest chat", 'YesNo', 'Question')
    if ($answer -ne 'Yes') { return }
    try {
        $raw = Get-Content $ControlFile -Raw -ErrorAction Stop
        $cfg = $raw | ConvertFrom-Json
        $cfg.guest.enabled = $false
        # Depth matters: the default of 2 would flatten the nested config into
        # type names and silently destroy the file.
        $cfg | ConvertTo-Json -Depth 10 | Set-Content $ControlFile -Encoding utf8 -ErrorAction Stop
        $p = Get-BotPid
        if ($p) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }
        $notify.ShowBalloonTip(5000, "Benham",
            "Guest chat disabled. Bot restarting - guests refused from the next start.", 'Info')
    } catch {
        [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, "Benham", 'OK', 'Error') | Out-Null
    }
} | Out-Null

Add-Item "Open guest search log" {
    $sl = Join-Path $Dir 'guest_searches.jsonl'
    if (Test-Path $sl) { Start-Process notepad.exe $sl }
    else { $notify.ShowBalloonTip(3000, "Benham", "No guest searches logged yet.", 'Info') }
} | Out-Null

Add-Item "Open supervise.log" {
    if (Test-Path $Log) { Start-Process notepad.exe $Log }
    else { $notify.ShowBalloonTip(3000, "Benham", "No supervise.log yet.", 'Info') }
} | Out-Null

Add-Item "Full status (status.py)" {
    # A console window is the point here - it is a report to read, not a background job.
    Start-Process powershell.exe -ArgumentList @(
        '-NoExit', '-NoProfile', '-Command',
        "Set-Location '$Dir'; python status.py; python guest.py status")
} | Out-Null

$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null

$appContext = New-Object System.Windows.Forms.ApplicationContext
Add-Item "Hide tray icon (bot keeps running)" {
    $notify.Visible = $false
    $appContext.ExitThread()
} | Out-Null

# --- poll -----------------------------------------------------------------

function Update-Tray {
    $botPid = Get-BotPid
    $supUp = Get-SupervisorUp
    $g = Get-GuestState

    if (-not $supUp) {
        $notify.Icon = $IconGrey
        $state = "supervisor OFF"
    } elseif ($botPid) {
        $notify.Icon = $IconGreen
        $state = "up (pid $botPid, $(Get-BotUptime $botPid))"
    } else {
        $notify.Icon = $IconRed
        $state = "DOWN - restart in flight"
    }

    if ($g.On) { $guestText = "Guest chat: ON ($($g.Count) whitelisted)" }
    else { $guestText = "Guest chat: off" }

    $miStatus.Text = "Benham: $state"
    $miGuest.Text = $guestText
    # NotifyIcon.Text throws over 63 chars, which would kill the timer thread.
    $tip = "Benham: $state`n$guestText"
    if ($tip.Length -gt 63) { $tip = $tip.Substring(0, 60) + "..." }
    $notify.Text = $tip
}

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 5000
$timer.add_Tick({ try { Update-Tray } catch {} })
$timer.Start()
Update-Tray

# Announce itself once. Windows 11 files a new tray icon into the hidden overflow by
# default, so without this a working app looks like a failed one - there is nothing to
# see until you know to click the chevron. The balloon appears regardless of overflow.
$notify.ShowBalloonTip(4000, "Benham tray",
    "Monitoring the supervisor. If you don't see the icon, it's under the taskbar's ^ chevron - drag it out to pin it.",
    'Info')

try {
    [System.Windows.Forms.Application]::Run($appContext)
} finally {
    $timer.Stop()
    $notify.Visible = $false
    $notify.Dispose()
    $IconGreen.Dispose(); $IconRed.Dispose(); $IconGrey.Dispose()
}
