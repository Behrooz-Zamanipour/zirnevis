$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    $studioPython = $null
    $studioArgs = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try {
            & py -3.12 -c "import sys,struct; assert sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8" 2>$null
            if ($LASTEXITCODE -eq 0) { $studioPython = 'py'; $studioArgs = @('-3.12') }
        } catch { }
    }
    if (-not $studioPython -and (Get-Command python -ErrorAction SilentlyContinue)) {
        try {
            & python -c "import sys,struct; assert sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8" 2>$null
            if ($LASTEXITCODE -eq 0) { $studioPython = 'python' }
        } catch { }
    }
    if (-not $studioPython) {
        throw 'Install Python 3.12 (Windows 64-bit) from python.org, enable the launcher or PATH option, then run this installer again. See docs/INSTALLATION.md.'
    }
    & $studioPython @studioArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment. See docs/INSTALLATION.md.' }
}

& '.venv/Scripts/python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Package installation failed. Check your internet connection and Python version, then retry.' }
& '.venv/Scripts/python.exe' prepare_models.py
if ($LASTEXITCODE -ne 0) { throw 'Model setup failed. Check the message above and run this installer again to resume.' }

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host 'Optional: install Node.js 22 or newer from nodejs.org to use YouTube links. File uploads work without it.'
}
Write-Host 'Ready. Double-click Start Studio.cmd to open Zirnevis.'
