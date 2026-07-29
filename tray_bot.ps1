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

function Get-GuestUsage {
    # Today's spend from guest_usage.json, plus the caps from control.json so the
    # line reads as "how close to the wall", not a bare number. A stale date means
    # nobody has messaged today - that is 0, not an error.
    try {
        $u = Get-Content (Join-Path $Dir 'guest_usage.json') -Raw -ErrorAction Stop | ConvertFrom-Json
        $caps = (Get-Content $ControlFile -Raw -ErrorAction Stop | ConvertFrom-Json).guest
        $today = (Get-Date).ToString('yyyy-MM-dd')
        $total = 0
        if ($u.date -eq $today -and $u.users) {
            foreach ($p in $u.users.PSObject.Properties) { $total += [int]$p.Value }
        }
        return "Guest usage today: $total msgs (caps $($caps.daily_message_cap)/guest, $($caps.global_daily_cap) global)"
    } catch { return "Guest usage today: ?" }
}

function Get-DmCount {
    # How many human DM lines inbox.jsonl holds. Compared against what the inbox
    # viewer has shown to decide whether the tray badge lights up. Full-file scan,
    # but the file is small and this runs every 5s on a machine that won't notice.
    try {
        $inbox = Join-Path $Dir 'inbox.jsonl'
        if (-not (Test-Path $inbox)) { return 0 }
        @(Select-String -Path $inbox -Pattern '"guild": null' -Encoding UTF8 |
            Where-Object { $_.Line -notmatch '"is_self": true' }).Count
    } catch { 0 }
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

function New-DotIcon([System.Drawing.Color]$color, [bool]$badge = $false) {
    $bmp = New-Object System.Drawing.Bitmap 16, 16
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = 'AntiAlias'
    $g.Clear([System.Drawing.Color]::Transparent)
    $brush = New-Object System.Drawing.SolidBrush $color
    $g.FillEllipse($brush, 1, 1, 14, 14)
    $pen = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(90, 0, 0, 0)), 1
    $g.DrawEllipse($pen, 1, 1, 14, 14)
    if ($badge) {
        # Unread-DM marker: a small orange dot bottom-right, same idea as every
        # messenger's notification badge.
        $bb = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 150, 20))
        $g.FillEllipse($bb, 9, 9, 7, 7)
        $bb.Dispose()
    }
    $brush.Dispose(); $pen.Dispose(); $g.Dispose()
    $icon = [System.Drawing.Icon]::FromHandle($bmp.GetHicon())
    $bmp.Dispose()
    return $icon
}

$IconGreen = New-DotIcon ([System.Drawing.Color]::FromArgb(60, 190, 90))
$IconRed = New-DotIcon ([System.Drawing.Color]::FromArgb(215, 70, 70))
$IconGrey = New-DotIcon ([System.Drawing.Color]::FromArgb(140, 140, 140))
$IconGreenDm = New-DotIcon ([System.Drawing.Color]::FromArgb(60, 190, 90)) $true
$IconRedDm = New-DotIcon ([System.Drawing.Color]::FromArgb(215, 70, 70)) $true
$IconGreyDm = New-DotIcon ([System.Drawing.Color]::FromArgb(140, 140, 140)) $true

# The viewer marks DMs read; start with everything current so a fresh tray does
# not badge history you have long since seen.
$script:DmSeen = Get-DmCount

# --- inbox viewer ---------------------------------------------------------
# A read-only window over inbox.jsonl: one line of JSON per message, rendered as
# a conversation instead of raw JSON. Reads the file fresh on open and on
# Refresh; it never writes anything, and closing it changes nothing.

$script:InboxForm = $null
$script:InboxDmOnly = $false

function Render-Inbox([System.Windows.Forms.RichTextBox]$rtb) {
    $inbox = Join-Path $Dir 'inbox.jsonl'
    $rtb.Clear()
    if (-not (Test-Path $inbox)) {
        $rtb.AppendText("No inbox.jsonl yet - the bot logs incoming messages there once it sees one.")
        return
    }
    # Tail, not the whole file: it grows forever and the window is for catching up,
    # not archaeology. UTF8 matters - the bot writes UTF-8 and 5.1's default read
    # would turn every dash and emoji into mojibake.
    $lines = Get-Content $inbox -Encoding UTF8 -Tail 300
    $lastDay = ''
    foreach ($line in $lines) {
        try { $m = $line | ConvertFrom-Json } catch { continue }
        if ($script:InboxDmOnly -and $null -ne $m.guild) { continue }
        try { $ts = [DateTimeOffset]::Parse($m.ts).ToLocalTime() } catch { $ts = $null }

        if ($ts -and $ts.ToString('yyyy-MM-dd') -ne $lastDay) {
            $lastDay = $ts.ToString('yyyy-MM-dd')
            $rtb.SelectionColor = [System.Drawing.Color]::Gray
            $rtb.SelectionFont = New-Object System.Drawing.Font('Segoe UI', 9, [System.Drawing.FontStyle]::Bold)
            $rtb.AppendText("--- $($ts.ToString('ddd yyyy-MM-dd')) ---`n")
        }

        $rtb.SelectionColor = [System.Drawing.Color]::Gray
        $rtb.SelectionFont = New-Object System.Drawing.Font('Consolas', 9)
        $when = if ($ts) { $ts.ToString('HH:mm') } else { '??:??' }
        $rtb.AppendText("$when  ")

        # Where: DMs stand out in orange, guild channels in teal.
        if ($null -eq $m.guild) {
            $rtb.SelectionColor = [System.Drawing.Color]::DarkOrange
            # The channel field is "Direct Message with <user>", which names the
            # counterpart - exactly what a DM line needs, since author alone is
            # ambiguous when it's Benham doing the sending.
            $who = "$($m.channel)" -replace '^Direct Message with ', ''
            $rtb.AppendText("[DM w/ $who] ")
        } else {
            $rtb.SelectionColor = [System.Drawing.Color]::Teal
            $rtb.AppendText("[$($m.guild) #$($m.channel)] ")
        }

        # Who: Benham's own messages dim so the humans pop.
        if ($m.is_self) {
            $rtb.SelectionColor = [System.Drawing.Color]::DarkGray
        } else {
            $rtb.SelectionColor = [System.Drawing.Color]::Black
        }
        $rtb.SelectionFont = New-Object System.Drawing.Font('Segoe UI', 9.5, [System.Drawing.FontStyle]::Bold)
        $rtb.AppendText("$($m.author): ")

        $rtb.SelectionFont = New-Object System.Drawing.Font('Segoe UI', 9.5)
        $content = if ([string]::IsNullOrEmpty($m.content)) { '(no text - attachment/embed only)' } else { $m.content }
        if ([string]::IsNullOrEmpty($m.content)) { $rtb.SelectionColor = [System.Drawing.Color]::Gray }
        $rtb.AppendText("$content`n")
    }
    if ($rtb.TextLength -eq 0) { $rtb.AppendText('inbox.jsonl is empty.') }
    $rtb.SelectionStart = $rtb.TextLength
    $rtb.ScrollToCaret()
    # Opening (or refreshing) the viewer is what "read" means here - clear the badge.
    $script:DmSeen = Get-DmCount
}

function Show-InboxWindow {
    # One window, re-fronted on repeat clicks - ten stacked copies of the same
    # inbox helps nobody.
    if ($script:InboxForm -and -not $script:InboxForm.IsDisposed) {
        Render-Inbox $script:InboxForm.Controls['rtb']
        $script:InboxForm.Activate()
        return
    }
    $form = New-Object System.Windows.Forms.Form
    $form.Text = 'Benham inbox (last 300 messages)'
    $form.Size = New-Object System.Drawing.Size(820, 560)
    $form.StartPosition = 'CenterScreen'

    $rtb = New-Object System.Windows.Forms.RichTextBox
    $rtb.Name = 'rtb'
    $rtb.ReadOnly = $true
    $rtb.DetectUrls = $false
    $rtb.BackColor = [System.Drawing.Color]::White
    $rtb.BorderStyle = 'None'
    $rtb.Dock = 'Fill'

    $bar = New-Object System.Windows.Forms.FlowLayoutPanel
    $bar.Dock = 'Bottom'
    $bar.Height = 34
    $bar.FlowDirection = 'RightToLeft'

    $chkDm = New-Object System.Windows.Forms.CheckBox
    $chkDm.Text = 'DMs only'
    $chkDm.Checked = [bool]$script:InboxDmOnly
    $chkDm.add_CheckedChanged({
        $script:InboxDmOnly = $this.Checked
        Render-Inbox $script:InboxForm.Controls['rtb']
    })

    $btnRefresh = New-Object System.Windows.Forms.Button
    $btnRefresh.Text = 'Refresh'
    $btnRefresh.add_Click({ Render-Inbox $script:InboxForm.Controls['rtb'] })
    $bar.Controls.Add($btnRefresh)

    $btnRaw = New-Object System.Windows.Forms.Button
    $btnRaw.Text = 'Open raw file'
    $btnRaw.add_Click({ Start-Process notepad.exe (Join-Path $Dir 'inbox.jsonl') })
    $bar.Controls.Add($btnRaw)
    $bar.Controls.Add($chkDm)

    $form.Controls.Add($rtb)
    $form.Controls.Add($bar)
    $script:InboxForm = $form
    Render-Inbox $rtb
    $form.Show()
}

# --- supervise.log viewer -------------------------------------------------
# Same idea as the inbox viewer: read-only window, tail of the file, a little
# color so the eye finds trouble - red for errors, green for starts, grey
# timestamps. Refresh re-reads; raw notepad stays one click away.

$script:LogForm = $null

function Render-Log([System.Windows.Forms.RichTextBox]$rtb) {
    $rtb.Clear()
    if (-not (Test-Path $Log)) {
        $rtb.AppendText("No supervise.log yet.")
        return
    }
    $lines = Get-Content $Log -Encoding UTF8 -Tail 500
    $reTs = '^\[([^\]]+)\]\s*(.*)$'
    foreach ($line in $lines) {
        $body = $line
        if ($line -match $reTs) {
            $rtb.SelectionColor = [System.Drawing.Color]::Gray
            $rtb.SelectionFont = New-Object System.Drawing.Font('Consolas', 9)
            $rtb.AppendText("[$($Matches[1])] ")
            $body = $Matches[2]
        }
        if ($body -match 'error|fail|exception|traceback|died|locked') {
            $rtb.SelectionColor = [System.Drawing.Color]::Firebrick
        } elseif ($body -match 'start|restart|logged in|launch') {
            $rtb.SelectionColor = [System.Drawing.Color]::SeaGreen
        } else {
            $rtb.SelectionColor = [System.Drawing.Color]::Black
        }
        $rtb.SelectionFont = New-Object System.Drawing.Font('Segoe UI', 9.5)
        $rtb.AppendText("$body`n")
    }
    if ($rtb.TextLength -eq 0) { $rtb.AppendText('supervise.log is empty.') }
    $rtb.SelectionStart = $rtb.TextLength
    $rtb.ScrollToCaret()
}

function Show-LogWindow {
    if ($script:LogForm -and -not $script:LogForm.IsDisposed) {
        Render-Log $script:LogForm.Controls['rtb']
        $script:LogForm.Activate()
        return
    }
    $form = New-Object System.Windows.Forms.Form
    $form.Text = 'Benham supervise.log (last 500 lines)'
    $form.Size = New-Object System.Drawing.Size(820, 560)
    $form.StartPosition = 'CenterScreen'

    $rtb = New-Object System.Windows.Forms.RichTextBox
    $rtb.Name = 'rtb'
    $rtb.ReadOnly = $true
    $rtb.DetectUrls = $false
    $rtb.BackColor = [System.Drawing.Color]::White
    $rtb.BorderStyle = 'None'
    $rtb.Dock = 'Fill'

    $bar = New-Object System.Windows.Forms.FlowLayoutPanel
    $bar.Dock = 'Bottom'
    $bar.Height = 34
    $bar.FlowDirection = 'RightToLeft'

    $btnRefresh = New-Object System.Windows.Forms.Button
    $btnRefresh.Text = 'Refresh'
    $btnRefresh.add_Click({ Render-Log $script:LogForm.Controls['rtb'] })
    $bar.Controls.Add($btnRefresh)

    $btnRaw = New-Object System.Windows.Forms.Button
    $btnRaw.Text = 'Open raw file'
    $btnRaw.add_Click({ Start-Process notepad.exe $Log })
    $bar.Controls.Add($btnRaw)

    $form.Controls.Add($rtb)
    $form.Controls.Add($bar)
    $script:LogForm = $form
    Render-Log $rtb
    $form.Show()
}

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
$miUsage = Add-Item "Guest usage today: ?" $null
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

Add-Item "View inbox" {
    Show-InboxWindow
} | Out-Null

Add-Item "Open guest search log" {
    $sl = Join-Path $Dir 'guest_searches.jsonl'
    if (Test-Path $sl) { Start-Process notepad.exe $sl }
    else { $notify.ShowBalloonTip(3000, "Benham", "No guest searches logged yet.", 'Info') }
} | Out-Null

Add-Item "Open Benhams-inbox folder" {
    # The pc.. workdir, where task artifacts land. Read from control.json each
    # click so a moved workdir does not leave the tray pointing at the old one.
    $wd = $null
    try { $wd = (Get-Content $ControlFile -Raw -ErrorAction Stop | ConvertFrom-Json).pc.workdir } catch {}
    if (-not $wd) { $wd = 'C:\Users\Tyler\Claude\Benhams-inbox' }
    if (Test-Path $wd) { Start-Process explorer.exe $wd }
    else { $notify.ShowBalloonTip(3000, "Benham", "Workdir not found: $wd", 'Warning') }
} | Out-Null

Add-Item "View supervise.log" {
    Show-LogWindow
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

    $dmNew = [Math]::Max(0, (Get-DmCount) - $script:DmSeen)

    if (-not $supUp) {
        $notify.Icon = if ($dmNew) { $IconGreyDm } else { $IconGrey }
        $state = "supervisor OFF"
    } elseif ($botPid) {
        $notify.Icon = if ($dmNew) { $IconGreenDm } else { $IconGreen }
        $state = "up (pid $botPid, $(Get-BotUptime $botPid))"
    } else {
        $notify.Icon = if ($dmNew) { $IconRedDm } else { $IconRed }
        $state = "DOWN - restart in flight"
    }

    if ($g.On) { $guestText = "Guest chat: ON ($($g.Count) whitelisted)" }
    else { $guestText = "Guest chat: off" }
    if ($dmNew) { $guestText += " - $dmNew new DM" + $(if ($dmNew -gt 1) { 's' }) }

    $miStatus.Text = "Benham: $state"
    $miGuest.Text = $guestText
    $miUsage.Text = Get-GuestUsage
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
