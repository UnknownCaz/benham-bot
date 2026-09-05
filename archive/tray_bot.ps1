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

# This script lives in scripts/; everything it touches is addressed from the repo root.
$Dir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Dir
$Log = Join-Path $Dir 'logs\supervise.log'
$ControlFile = Join-Path $Dir 'config\control.json'
$TaskName = 'benham-bot'

# --- state readers (all read-only) ----------------------------------------

function Get-BotPid {
    # The same CIM query status.py and supervise_bot.ps1 use, so all three agree on
    # what "the bot is running" means.
    try {
        Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction Stop |
            Where-Object { $_.CommandLine -match '-m benham\.bot' } |
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
        $u = Get-Content (Join-Path $Dir 'state\guest_usage.json') -Raw -ErrorAction Stop | ConvertFrom-Json
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
        $inbox = Join-Path $Dir 'state\inbox.jsonl'
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
#
# The art is a stripped-down Benham signet - the octagon plate and B from
# assets/benham-discord-avatar.svg in its own maroon/orange - with status
# carried by a small dot bottom-right (the old green/red/grey language) and
# unread DMs by an orange dot top-left.
#
# Sizing: the tray slot is SystemInformation.SmallIconSize (16 at 96dpi, larger
# at higher scaling). Handing the shell any other size invites its rescaling -
# a fixed 32px version came back visibly shrunken. So render 4x supersampled in
# a 32-unit art space, then downscale once, ourselves, to the exact slot size.

function New-SignetIcon([System.Drawing.Color]$statusColor, [bool]$badge = $false) {
    $slot = [System.Windows.Forms.SystemInformation]::SmallIconSize.Width
    if ($slot -lt 16) { $slot = 16 }

    $ss = New-Object System.Drawing.Bitmap 128, 128
    $g = [System.Drawing.Graphics]::FromImage($ss)
    $g.SmoothingMode = 'AntiAlias'
    $g.TextRenderingHint = 'AntiAlias'
    $g.Clear([System.Drawing.Color]::Transparent)
    $g.ScaleTransform(4, 4)

    $plate = [System.Drawing.Color]::FromArgb(151, 58, 55)     # seal face
    $keyline = [System.Drawing.Color]::FromArgb(221, 122, 47)  # keyline + B
    $ringCol = [System.Drawing.Color]::FromArgb(28, 28, 28)    # dot separator

    # Octagon plate: flats at 0/45/90 like the avatar. Vertex radius 14.8 fills
    # the canvas edge-to-edge (keyline pen adds ~1 outward, landing on ~15.8).
    $pts = New-Object 'System.Drawing.PointF[]' 8
    for ($k = 0; $k -lt 8; $k++) {
        $a = (22.5 + 45 * $k) * [Math]::PI / 180
        $pts[$k] = [System.Drawing.PointF]::new(16 + 14.8 * [Math]::Cos($a), 16 + 14.8 * [Math]::Sin($a))
    }
    $brush = New-Object System.Drawing.SolidBrush $plate
    $g.FillPolygon($brush, $pts)
    $pen = New-Object System.Drawing.Pen $keyline, 2
    $pen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
    $g.DrawPolygon($pen, $pts)

    # The B, centered on the plate.
    $font = New-Object System.Drawing.Font('Segoe UI', 19, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
    $sf = New-Object System.Drawing.StringFormat
    $sf.Alignment = [System.Drawing.StringAlignment]::Center
    $sf.LineAlignment = [System.Drawing.StringAlignment]::Center
    $bBrush = New-Object System.Drawing.SolidBrush $keyline
    $rect = New-Object System.Drawing.RectangleF 0, 1, 32, 32
    $g.DrawString('B', $font, $bBrush, $rect, $sf)

    # Status dot bottom-right: a dark ring first so it reads as its own element
    # against both the plate and whatever the taskbar is doing behind it.
    # Kept fully inside the canvas (0.5 margin) - an earlier version ran 0.5
    # past the edge, clipped, and dragged the icon's visual weight low-right.
    $rb = New-Object System.Drawing.SolidBrush $ringCol
    $sb = New-Object System.Drawing.SolidBrush $statusColor
    $g.FillEllipse($rb, 18.5, 18.5, 13, 13)
    $g.FillEllipse($sb, 20.0, 20.0, 10, 10)

    if ($badge) {
        # Unread-DM marker top-left, mirror of the status dot.
        $bb = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 150, 20))
        $g.FillEllipse($rb, 0.5, 0.5, 13, 13)
        $g.FillEllipse($bb, 2.0, 2.0, 10, 10)
        $bb.Dispose()
    }

    $brush.Dispose(); $pen.Dispose(); $font.Dispose(); $sf.Dispose()
    $bBrush.Dispose(); $rb.Dispose(); $sb.Dispose(); $g.Dispose()

    # One clean downscale to the slot size - the shell gets exactly what it asked
    # for and never rescales.
    $bmp = New-Object System.Drawing.Bitmap $slot, $slot
    $g2 = [System.Drawing.Graphics]::FromImage($bmp)
    $g2.InterpolationMode = 'HighQualityBicubic'
    $g2.PixelOffsetMode = 'HighQuality'
    $g2.DrawImage($ss, 0, 0, $slot, $slot)
    $g2.Dispose(); $ss.Dispose()

    $icon = [System.Drawing.Icon]::FromHandle($bmp.GetHicon())
    $bmp.Dispose()
    return $icon
}

$IconGreen = New-SignetIcon ([System.Drawing.Color]::FromArgb(60, 190, 90))
$IconRed = New-SignetIcon ([System.Drawing.Color]::FromArgb(215, 70, 70))
$IconGrey = New-SignetIcon ([System.Drawing.Color]::FromArgb(140, 140, 140))
$IconGreenDm = New-SignetIcon ([System.Drawing.Color]::FromArgb(60, 190, 90)) $true
$IconRedDm = New-SignetIcon ([System.Drawing.Color]::FromArgb(215, 70, 70)) $true
$IconGreyDm = New-SignetIcon ([System.Drawing.Color]::FromArgb(140, 140, 140)) $true

# The viewer marks DMs read; start with everything current so a fresh tray does
# not badge history you have long since seen.
$script:DmSeen = Get-DmCount

# True only after "Stop bot until reboot" was used, so the status row can say why
# the supervisor is off. Cleared whenever the supervisor is seen running again.
$script:StoppedByTray = $false

# --- viewer theme ---------------------------------------------------------
# The inbox and supervise.log windows: neutral charcoal, deliberately NOT the
# menu's plum - long reads want a quiet terminal, not a brand statement. One
# accent color per line, everything else stays in the cream/grey family.
# Fonts are built once here instead of per-AppendText like the old renderers.

Add-Type -Namespace Benham -Name Dark -MemberDefinition @'
[DllImport("dwmapi.dll")]
public static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);
[DllImport("uxtheme.dll", CharSet = CharSet.Unicode)]
public static extern int SetWindowTheme(IntPtr hWnd, string appName, string subIdList);
'@

$VwBack = [System.Drawing.Color]::FromArgb(30, 30, 30)      # window + text bg
$VwBar = [System.Drawing.Color]::FromArgb(38, 38, 38)       # bottom button bar
$VwBtn = [System.Drawing.Color]::FromArgb(45, 45, 45)       # button face
$VwEdge = [System.Drawing.Color]::FromArgb(58, 58, 58)      # button border
$VwText = [System.Drawing.Color]::FromArgb(214, 210, 204)   # body cream
$VwMuted = [System.Drawing.Color]::FromArgb(138, 134, 128)  # timestamps, self
$VwBand = [System.Drawing.Color]::FromArgb(45, 42, 40)      # day-header band
$VwHeader = [System.Drawing.Color]::FromArgb(232, 201, 168) # day-header text
$VwOrange = [System.Drawing.Color]::FromArgb(221, 122, 47)  # DM tag
$VwTeal = [System.Drawing.Color]::FromArgb(93, 202, 165)    # guild tag
$VwRed = [System.Drawing.Color]::FromArgb(226, 75, 74)      # log errors
$VwGreen = [System.Drawing.Color]::FromArgb(151, 196, 89)   # log starts

$VwFontMeta = New-Object System.Drawing.Font('Consolas', 10)
$VwFontBody = New-Object System.Drawing.Font('Segoe UI', 10.5)
$VwFontBodyBold = New-Object System.Drawing.Font('Segoe UI', 10.5, [System.Drawing.FontStyle]::Bold)
$VwFontHeader = New-Object System.Drawing.Font('Segoe UI', 10, [System.Drawing.FontStyle]::Bold)
$VwFontSpacer = New-Object System.Drawing.Font('Segoe UI', 4)

function Set-DarkChrome($form, $rtb) {
    # Win11 niceties: dark title bar (DWM attribute 20) and dark scrollbars on
    # the text control. Both fail harmlessly on older builds.
    try {
        $v = 1
        [Benham.Dark]::DwmSetWindowAttribute($form.Handle, 20, [ref]$v, 4) | Out-Null
        [Benham.Dark]::SetWindowTheme($rtb.Handle, 'DarkMode_Explorer', $null) | Out-Null
    } catch {}
}

function New-ViewerButton($text, $action) {
    $b = New-Object System.Windows.Forms.Button
    $b.Text = $text
    $b.FlatStyle = 'Flat'
    $b.FlatAppearance.BorderColor = $VwEdge
    $b.BackColor = $VwBtn
    $b.ForeColor = $VwText
    $b.add_Click($action)
    return $b
}

# --- inbox viewer ---------------------------------------------------------
# A read-only window over inbox.jsonl: one line of JSON per message, rendered as
# a conversation instead of raw JSON. Reads the file fresh on open and on
# Refresh; it never writes anything, and closing it changes nothing.

$script:InboxForm = $null
$script:InboxDmOnly = $false

function Render-Inbox([System.Windows.Forms.RichTextBox]$rtb) {
    $inbox = Join-Path $Dir 'state\inbox.jsonl'
    $rtb.Clear()
    # A small gutter so text does not hug the window edge.
    $rtb.SelectionIndent = 10
    $rtb.SelectionRightIndent = 10
    if (-not (Test-Path $inbox)) {
        $rtb.SelectionColor = $VwMuted
        $rtb.SelectionFont = $VwFontBody
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
            # Day band: a padded highlight row the eye can find mid-scroll.
            $rtb.SelectionFont = $VwFontSpacer
            $rtb.AppendText("`n")
            $rtb.SelectionBackColor = $VwBand
            $rtb.SelectionColor = $VwHeader
            $rtb.SelectionFont = $VwFontHeader
            $rtb.AppendText("  $($ts.ToString('ddd  yyyy-MM-dd'))  ")
            $rtb.SelectionBackColor = $VwBack
            $rtb.SelectionFont = $VwFontSpacer
            $rtb.AppendText("`n`n")
        }

        $rtb.SelectionBackColor = $VwBack
        $rtb.SelectionColor = $VwMuted
        $rtb.SelectionFont = $VwFontMeta
        $when = if ($ts) { $ts.ToString('HH:mm') } else { '??:??' }
        $rtb.AppendText("$when  ")

        # Where: one accent per line - DMs orange, guild channels teal. The
        # channel field is "Direct Message with <user>", which names the
        # counterpart - exactly what a DM line needs, since author alone is
        # ambiguous when it's Benham doing the sending.
        if ($null -eq $m.guild) {
            $rtb.SelectionColor = $VwOrange
            $who = "$($m.channel)" -replace '^Direct Message with ', ''
            $rtb.AppendText("DM $who  ")
        } else {
            $rtb.SelectionColor = $VwTeal
            $rtb.AppendText("$($m.guild) #$($m.channel)  ")
        }

        # Who: Benham's own messages dim so the humans pop.
        if ($m.is_self) { $authorColor = $VwMuted } else { $authorColor = $VwText }
        $rtb.SelectionColor = $authorColor
        $rtb.SelectionFont = $VwFontBodyBold
        $rtb.AppendText("$($m.author)  ")

        $rtb.SelectionFont = $VwFontBody
        if ([string]::IsNullOrEmpty($m.content)) {
            $rtb.SelectionColor = $VwMuted
            $rtb.AppendText("(no text - attachment/embed only)`n")
        } else {
            $rtb.SelectionColor = $authorColor
            $rtb.AppendText("$($m.content)`n")
        }
        # Breathing room between messages.
        $rtb.SelectionFont = $VwFontSpacer
        $rtb.AppendText("`n")
    }
    if ($rtb.TextLength -eq 0) {
        $rtb.SelectionColor = $VwMuted
        $rtb.SelectionFont = $VwFontBody
        $rtb.AppendText('inbox.jsonl is empty.')
    }
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
    $form.Size = New-Object System.Drawing.Size(860, 600)
    $form.StartPosition = 'CenterScreen'
    $form.BackColor = $VwBack

    $rtb = New-Object System.Windows.Forms.RichTextBox
    $rtb.Name = 'rtb'
    $rtb.ReadOnly = $true
    $rtb.DetectUrls = $false
    $rtb.BackColor = $VwBack
    $rtb.ForeColor = $VwText
    $rtb.BorderStyle = 'None'
    $rtb.Dock = 'Fill'

    $bar = New-Object System.Windows.Forms.FlowLayoutPanel
    $bar.Dock = 'Bottom'
    $bar.Height = 38
    $bar.FlowDirection = 'RightToLeft'
    $bar.BackColor = $VwBar

    $chkDm = New-Object System.Windows.Forms.CheckBox
    $chkDm.Text = 'DMs only'
    $chkDm.ForeColor = $VwText
    $chkDm.Checked = [bool]$script:InboxDmOnly
    $chkDm.add_CheckedChanged({
        $script:InboxDmOnly = $this.Checked
        Render-Inbox $script:InboxForm.Controls['rtb']
    })

    $bar.Controls.Add((New-ViewerButton 'Refresh' { Render-Inbox $script:InboxForm.Controls['rtb'] }))
    $bar.Controls.Add((New-ViewerButton 'Open raw file' { Start-Process notepad.exe (Join-Path $Dir 'state\inbox.jsonl') }))
    $bar.Controls.Add($chkDm)

    $form.Controls.Add($rtb)
    $form.Controls.Add($bar)
    $script:InboxForm = $form
    Set-DarkChrome $form $rtb
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
    $rtb.SelectionIndent = 10
    $rtb.SelectionRightIndent = 10
    if (-not (Test-Path $Log)) {
        $rtb.SelectionColor = $VwMuted
        $rtb.SelectionFont = $VwFontBody
        $rtb.AppendText("No supervise.log yet.")
        return
    }
    $lines = Get-Content $Log -Encoding UTF8 -Tail 500
    $reTs = '^\[([^\]]+)\]\s*(.*)$'
    foreach ($line in $lines) {
        $body = $line
        if ($line -match $reTs) {
            $rtb.SelectionColor = $VwMuted
            $rtb.SelectionFont = $VwFontMeta
            $rtb.AppendText("$($Matches[1])  ")
            $body = $Matches[2]
        }
        # Only trouble and starts get color - routine lines stay cream so the
        # exceptions are the things that glow. Errors also go bold.
        if ($body -match 'error|fail|exception|traceback|died|locked') {
            $rtb.SelectionColor = $VwRed
            $rtb.SelectionFont = $VwFontBodyBold
        } elseif ($body -match 'start|restart|logged in|launch') {
            $rtb.SelectionColor = $VwGreen
            $rtb.SelectionFont = $VwFontBody
        } else {
            $rtb.SelectionColor = $VwText
            $rtb.SelectionFont = $VwFontBody
        }
        $rtb.AppendText("$body`n")
        # Breathing room between entries.
        $rtb.SelectionFont = $VwFontSpacer
        $rtb.AppendText("`n")
    }
    if ($rtb.TextLength -eq 0) {
        $rtb.SelectionColor = $VwMuted
        $rtb.SelectionFont = $VwFontBody
        $rtb.AppendText('supervise.log is empty.')
    }
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
    $form.Size = New-Object System.Drawing.Size(860, 600)
    $form.StartPosition = 'CenterScreen'
    $form.BackColor = $VwBack

    $rtb = New-Object System.Windows.Forms.RichTextBox
    $rtb.Name = 'rtb'
    $rtb.ReadOnly = $true
    $rtb.DetectUrls = $false
    $rtb.BackColor = $VwBack
    $rtb.ForeColor = $VwText
    $rtb.BorderStyle = 'None'
    $rtb.Dock = 'Fill'

    $bar = New-Object System.Windows.Forms.FlowLayoutPanel
    $bar.Dock = 'Bottom'
    $bar.Height = 38
    $bar.FlowDirection = 'RightToLeft'
    $bar.BackColor = $VwBar

    $bar.Controls.Add((New-ViewerButton 'Refresh' { Render-Log $script:LogForm.Controls['rtb'] }))
    $bar.Controls.Add((New-ViewerButton 'Open raw file' { Start-Process notepad.exe $Log }))

    $form.Controls.Add($rtb)
    $form.Controls.Add($bar)
    $script:LogForm = $form
    Set-DarkChrome $form $rtb
    Render-Log $rtb
    $form.Show()
}

# --- menu theme -----------------------------------------------------------
# Signet dark: the avatar's plum/maroon/orange applied through a custom
# renderer. ToolStripManager.Renderer themes every strip in this process,
# submenu flyouts included. The C# compiles once at startup (~a second).
#
# $MenuFontName is the one knob for typeface experiments - 'Georgia' or
# 'Cambria' lean into the signet look, 'Consolas' goes terminal.
$MenuFontName = 'Segoe UI'
$MenuFontSize = 9.5

Add-Type -ReferencedAssemblies System.Windows.Forms, System.Drawing -TypeDefinition @'
using System.Drawing;
using System.Windows.Forms;

public class BenhamColorTable : ProfessionalColorTable
{
    private static readonly Color Plum = Color.FromArgb(82, 48, 65);
    private static readonly Color Maroon = Color.FromArgb(103, 54, 56);
    private static readonly Color Orange = Color.FromArgb(221, 122, 47);

    public override Color ToolStripDropDownBackground { get { return Plum; } }
    public override Color ImageMarginGradientBegin { get { return Plum; } }
    public override Color ImageMarginGradientMiddle { get { return Plum; } }
    public override Color ImageMarginGradientEnd { get { return Plum; } }
    public override Color MenuItemSelected { get { return Orange; } }
    public override Color MenuItemSelectedGradientBegin { get { return Orange; } }
    public override Color MenuItemSelectedGradientEnd { get { return Orange; } }
    public override Color MenuItemBorder { get { return Orange; } }
    public override Color MenuItemPressedGradientBegin { get { return Maroon; } }
    public override Color MenuItemPressedGradientMiddle { get { return Maroon; } }
    public override Color MenuItemPressedGradientEnd { get { return Maroon; } }
    public override Color MenuBorder { get { return Maroon; } }
    public override Color SeparatorDark { get { return Maroon; } }
    public override Color SeparatorLight { get { return Plum; } }
}

public class BenhamRenderer : ToolStripProfessionalRenderer
{
    private static readonly Color Cream = Color.FromArgb(242, 229, 218);
    private static readonly Color HeaderCream = Color.FromArgb(232, 201, 168);
    private static readonly Color HoverInk = Color.FromArgb(58, 31, 45);
    private static readonly Color MutedRose = Color.FromArgb(201, 160, 143);

    public BenhamRenderer() : base(new BenhamColorTable())
    {
        this.RoundedEdges = false;
    }

    protected override void OnRenderItemText(ToolStripItemTextRenderEventArgs e)
    {
        // Base paints disabled items in SystemColors.GrayText no matter what,
        // which is mud on plum - draw those ourselves. Bold + disabled is the
        // header row; plain disabled are the guest status rows.
        if (!e.Item.Enabled)
        {
            Color c = e.Item.Font.Bold ? HeaderCream : MutedRose;
            TextRenderer.DrawText(e.Graphics, e.Text, e.TextFont, e.TextRectangle, c, e.TextFormat);
            return;
        }
        if (e.Item.Pressed) { e.TextColor = Cream; }          // open submenu parent, maroon bg
        else if (e.Item.Selected) { e.TextColor = HoverInk; } // hover, orange bg
        else { e.TextColor = Cream; }
        base.OnRenderItemText(e);
    }

    protected override void OnRenderArrow(ToolStripArrowRenderEventArgs e)
    {
        e.ArrowColor = (e.Item.Selected && !e.Item.Pressed) ? HoverInk : MutedRose;
        base.OnRenderArrow(e);
    }
}
'@

[System.Windows.Forms.ToolStripManager]::Renderer = New-Object BenhamRenderer

# --- tray -----------------------------------------------------------------

$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = $IconGrey
$notify.Text = "Benham: checking..."
$notify.Visible = $true

$menu = New-Object System.Windows.Forms.ContextMenuStrip
$menu.Font = New-Object System.Drawing.Font($MenuFontName, $MenuFontSize)
$notify.ContextMenuStrip = $menu

function Add-Item($text, $action) {
    $i = $menu.Items.Add($text)
    if ($action) { $i.add_Click($action) } else { $i.Enabled = $false }
    return $i
}

function New-SubItem($parent, $text, $action) {
    # Same contract as Add-Item, but into a submenu's dropdown.
    $i = New-Object System.Windows.Forms.ToolStripMenuItem $text
    if ($action) { $i.add_Click($action) } else { $i.Enabled = $false }
    $parent.DropDownItems.Add($i) | Out-Null
    return $i
}

# One bold header row carries the whole at-a-glance story; the guest detail
# rows that used to sit here live in the Guest submenu now.
$miHeader = Add-Item "Benham: checking..." $null
$miHeader.Font = New-Object System.Drawing.Font($MenuFontName, $MenuFontSize, [System.Drawing.FontStyle]::Bold)
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

Add-Item "Stop bot until reboot" {
    # Stops the supervisor task so nothing revives the bot, then kills the bot.
    # The task is logon-triggered, so "until reboot" needs no extra state - and
    # "Restart bot" above already knows how to start the task again sooner.
    $answer = [System.Windows.Forms.MessageBox]::Show(
        "Stop the supervisor and bot until the next reboot (or until you click Restart bot)?",
        "Benham - stop bot", 'YesNo', 'Question')
    if ($answer -ne 'Yes') { return }
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    # Task Scheduler is supposed to take the process tree down with the task, but
    # kill the bot process explicitly rather than trust that.
    $p = Get-BotPid
    if ($p) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }
    $script:StoppedByTray = $true
    $notify.ShowBalloonTip(5000, "Benham",
        "Bot stopped until reboot. Use 'Restart bot' to bring it back.", 'Info')
    Update-Tray
} | Out-Null

$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null

Add-Item "View inbox" {
    Show-InboxWindow
} | Out-Null

# Everything guest-related in one flyout: the status rows, the off switch, and
# the audit trail.
$miGuestMenu = New-Object System.Windows.Forms.ToolStripMenuItem "Guest"
$menu.Items.Add($miGuestMenu) | Out-Null
# Flyouts do not reliably inherit the parent strip's font - set it explicitly.
$miGuestMenu.DropDown.Font = $menu.Font

$miGuest = New-SubItem $miGuestMenu "Guest chat: ?" $null
$miUsage = New-SubItem $miGuestMenu "Guest usage today: ?" $null
$miGuestMenu.DropDownItems.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null

New-SubItem $miGuestMenu "Disable guest chat + restart" {
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

New-SubItem $miGuestMenu "Open search log" {
    $sl = Join-Path $Dir 'state\guest_searches.jsonl'
    if (Test-Path $sl) { Start-Process notepad.exe $sl }
    else { $notify.ShowBalloonTip(3000, "Benham", "No guest searches logged yet.", 'Info') }
} | Out-Null

$miLogsMenu = New-Object System.Windows.Forms.ToolStripMenuItem "Logs"
$menu.Items.Add($miLogsMenu) | Out-Null
$miLogsMenu.DropDown.Font = $menu.Font

New-SubItem $miLogsMenu "View supervise.log" {
    Show-LogWindow
} | Out-Null

New-SubItem $miLogsMenu "Full status (benham.py status)" {
    # A console window is the point here - it is a report to read, not a background job.
    Start-Process powershell.exe -ArgumentList @(
        '-NoExit', '-NoProfile', '-Command',
        "Set-Location '$Dir'; python benham.py status; python benham.py guest status")
} | Out-Null

$miOpenMenu = New-Object System.Windows.Forms.ToolStripMenuItem "Open"
$menu.Items.Add($miOpenMenu) | Out-Null
$miOpenMenu.DropDown.Font = $menu.Font

New-SubItem $miOpenMenu "Manual" {
    # The owner's manual - a local HTML site in docs/. Opens in the default browser.
    $manual = Join-Path $Dir 'docs\index.html'
    if (Test-Path $manual) { Start-Process $manual }
    else { $notify.ShowBalloonTip(3000, "Benham", "Manual not found: $manual", 'Warning') }
} | Out-Null

New-SubItem $miOpenMenu "Benhams-inbox folder" {
    # The pc.. workdir, where task artifacts land. Read from control.json each
    # click so a moved workdir does not leave the tray pointing at the old one.
    $wd = $null
    try { $wd = (Get-Content $ControlFile -Raw -ErrorAction Stop | ConvertFrom-Json).pc.workdir } catch {}
    if (-not $wd) { $wd = 'C:\Users\Tyler\Claude\Benhams-inbox' }
    if (Test-Path $wd) { Start-Process explorer.exe $wd }
    else { $notify.ShowBalloonTip(3000, "Benham", "Workdir not found: $wd", 'Warning') }
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

    # Supervisor back by any path (Restart bot, shell, reboot) means the
    # stopped-by-tray explanation no longer applies.
    if ($supUp) { $script:StoppedByTray = $false }

    if (-not $supUp) {
        $notify.Icon = if ($dmNew) { $IconGreyDm } else { $IconGrey }
        $state = if ($script:StoppedByTray) { "stopped until reboot (via tray)" } else { "supervisor OFF" }
    } elseif ($botPid) {
        $notify.Icon = if ($dmNew) { $IconGreenDm } else { $IconGreen }
        $state = "up (pid $botPid, $(Get-BotUptime $botPid))"
    } else {
        $notify.Icon = if ($dmNew) { $IconRedDm } else { $IconRed }
        $state = "DOWN - restart in flight"
    }

    if ($g.On) { $guestText = "Guest chat: ON ($($g.Count) whitelisted)" }
    else { $guestText = "Guest chat: off" }
    # New DMs belong on the header - they are about Benham, not about guests.
    $dmText = if ($dmNew) { " - $dmNew new DM" + $(if ($dmNew -gt 1) { 's' }) } else { '' }

    $miHeader.Text = "Benham: $state$dmText"
    $miGuest.Text = $guestText
    $miUsage.Text = Get-GuestUsage
    # NotifyIcon.Text throws over 63 chars, which would kill the timer thread.
    $tip = "Benham: $state$dmText`n$guestText"
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
} catch {
    # Crash net: a hidden-window launch gives errors nowhere to go, so write
    # the full story to a file and say so out loud before dying.
    $crash = "$(Get-Date -Format o)`n$($_.Exception)`n$($_.InvocationInfo.PositionMessage)`n$($_.ScriptStackTrace)"
    try { $crash | Set-Content (Join-Path $Dir 'logs\tray-crash.log') -Encoding utf8 } catch {}
    try {
        [System.Windows.Forms.MessageBox]::Show(
            "The tray crashed - details in logs\tray-crash.log`n`n$($_.Exception.Message)",
            "Benham tray - crashed", 'OK', 'Error') | Out-Null
    } catch {}
} finally {
    $timer.Stop()
    $notify.Visible = $false
    $notify.Dispose()
    $IconGreen.Dispose(); $IconRed.Dispose(); $IconGrey.Dispose()
}
