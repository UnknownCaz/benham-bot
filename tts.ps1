param(
    [Parameter(Mandatory = $true)][string]$TextFile,
    [Parameter(Mandatory = $true)][string]$OutFile
)
# Windows SAPI text-to-speech -> WAV. Text is read from a file to avoid quoting issues.
# Voice/rate/volume come from voice_settings.json (edit that to change how the bot sounds):
#   voice  = installed SAPI voice name (e.g. "Microsoft David Desktop", "Microsoft Zira Desktop")
#   rate   = -10 (slowest) .. 10 (fastest)
#   volume = 0 .. 100
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer

$settingsPath = Join-Path $PSScriptRoot 'voice_settings.json'
if (Test-Path $settingsPath) {
    try {
        $cfg = Get-Content -Raw -Encoding UTF8 $settingsPath | ConvertFrom-Json
        if ($cfg.voice) { try { $synth.SelectVoice($cfg.voice) } catch {} }
        if ($null -ne $cfg.rate) { $synth.Rate = [int]$cfg.rate }
        if ($null -ne $cfg.volume) { $synth.Volume = [int]$cfg.volume }
    } catch {}
}

$synth.SetOutputToWaveFile($OutFile)
$text = Get-Content -Raw -Encoding UTF8 $TextFile
$synth.Speak($text)
$synth.Dispose()
