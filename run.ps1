$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir
. .\venv\Scripts\Activate.ps1
python main.py
if ($LASTEXITCODE -ne 0) { Read-Host "Press Enter to exit" }
