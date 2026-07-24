param(
    [Parameter(Mandatory = $true)][string]$TextFile,
    [Parameter(Mandatory = $true)][string]$OutFile
)
# Windows SAPI text-to-speech -> WAV. Text is read from a file to avoid quoting issues.
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.SetOutputToWaveFile($OutFile)
$text = Get-Content -Raw -Encoding UTF8 $TextFile
$synth.Speak($text)
$synth.Dispose()
