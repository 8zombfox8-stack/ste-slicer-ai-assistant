# Builds "AI Assistant.exe" with the C# compiler from .NET Framework 4 (part of Windows 10/11) and draws its icon.
# Usage: powershell -ExecutionPolicy Bypass -File launcher\build_launcher.ps1

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) { $csc = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe" }

# Icon: green diamond on the chat's dark background
Add-Type -AssemblyName System.Drawing
$icon = Join-Path $here "AIAssistant.ico"
$bmp = New-Object System.Drawing.Bitmap 64, 64
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode = "AntiAlias"
$g.Clear([System.Drawing.Color]::FromArgb(21, 42, 54))
$points = [System.Drawing.Point[]]@((New-Object System.Drawing.Point 32, 6), (New-Object System.Drawing.Point 58, 32),
                                    (New-Object System.Drawing.Point 32, 58), (New-Object System.Drawing.Point 6, 32))
$g.FillPolygon((New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(164, 200, 28))), $points)
$font = New-Object System.Drawing.Font "Segoe UI", 15, ([System.Drawing.FontStyle]::Bold)
$format = New-Object System.Drawing.StringFormat
$format.Alignment = "Center"; $format.LineAlignment = "Center"
$g.DrawString("AI", $font, (New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(19, 34, 10))),
              (New-Object System.Drawing.RectangleF 0, 0, 64, 64), $format)
$g.Dispose()
$stream = [System.IO.File]::Create($icon)
[System.Drawing.Icon]::FromHandle($bmp.GetHicon()).Save($stream)
$stream.Close()

$out = Join-Path $here "AI Assistant.exe"
& $csc /nologo /target:winexe /optimize+ "/win32icon:$icon" "/out:$out" `
    /reference:System.Windows.Forms.dll /reference:System.Drawing.dll (Join-Path $here "AIAssistantLauncher.cs")
if ($LASTEXITCODE -ne 0) { throw "Сборка не удалась" }
Write-Host "Собрано: $out"
