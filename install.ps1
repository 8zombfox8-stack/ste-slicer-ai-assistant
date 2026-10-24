# Installs the AI Assistant plugin into STE Slicer's user plugin folder.
# Usage: double-click install.bat (or: powershell -ExecutionPolicy Bypass -File install.ps1)
param([string]$ConfigRoot = (Join-Path $env:APPDATA "steslicer"))

$ErrorActionPreference = "Stop"
$source = Join-Path $PSScriptRoot "AIAssistant"

function Fail($message) {
    Write-Host ""
    Write-Host "ОШИБКА: $message" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path (Join-Path $source "plugin.json"))) {
    Fail "Рядом с install.bat нет папки AIAssistant с plugin.json. Распакуйте архив целиком и запустите install.bat из него."
}
if (-not (Test-Path $ConfigRoot)) {
    Fail "Не найдена папка настроек STE Slicer ($ConfigRoot). Установите STE Slicer, запустите его один раз, закройте и повторите."
}

# The newest version folder, e.g. 7.10 (compared as versions, not as text).
$versionDir = Get-ChildItem $ConfigRoot -Directory |
    Where-Object { $_.Name -match '^\d+(\.\d+)*$' } |
    Sort-Object { [version]($(if ($_.Name -match '\.') { $_.Name } else { "$($_.Name).0" })) } -Descending |
    Select-Object -First 1
if ($null -eq $versionDir) {
    Fail "В $ConfigRoot нет папки версии (например 7.10). Запустите STE Slicer один раз, закройте и повторите."
}

$plugins = Join-Path $versionDir.FullName "plugins"
$target = Join-Path $plugins "AIAssistant"
New-Item -ItemType Directory -Force $plugins | Out-Null
if (Test-Path $target) {
    Remove-Item $target -Recurse -Force   # replace the previous version of this plugin only
}
Copy-Item $source $target -Recurse
Get-ChildItem $target -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Get-ChildItem $target -Recurse -File | Unblock-File -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Плагин установлен: $target" -ForegroundColor Green

# The "AI Assistant" app: launcher + shortcuts on the desktop and in the Start menu
$launcher = Join-Path $PSScriptRoot "launcher\AI Assistant.exe"
if (Test-Path $launcher) {
    $appDir = Join-Path $env:LOCALAPPDATA "STE_AI_Assistant"
    New-Item -ItemType Directory -Force $appDir | Out-Null
    $appExe = Join-Path $appDir "AI Assistant.exe"
    Copy-Item $launcher $appExe -Force
    # Files from a downloaded ZIP carry the "from the Internet" mark; without it Windows does not show
    # the SmartScreen warning on every start of the app.
    Unblock-File $appExe -ErrorAction SilentlyContinue
    $shell = New-Object -ComObject WScript.Shell
    $links = @([Environment]::GetFolderPath("Desktop"), (Join-Path ([Environment]::GetFolderPath("Programs")) ""))
    foreach ($folder in $links) {
        $link = $shell.CreateShortcut((Join-Path $folder "AI Assistant.lnk"))
        $link.TargetPath = $appExe
        $link.WorkingDirectory = $appDir
        $link.Description = "STE Slicer + AI Assistant"
        $link.Save()
    }
    Write-Host "Приложение установлено: значок «AI Assistant» на рабочем столе и в меню «Пуск»." -ForegroundColor Green
}

if (Get-Process STESlicer -ErrorAction SilentlyContinue) {
    Write-Host "STE Slicer сейчас запущен — закройте его и запустите через значок «AI Assistant»." -ForegroundColor Yellow
}
Write-Host "Дальше: запустите «AI Assistant» с рабочего стола и укажите в «Настройках» адрес, ключ и модель LLM."
