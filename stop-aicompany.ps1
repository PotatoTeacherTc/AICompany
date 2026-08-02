$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtime = Join-Path $projectRoot "Automation\product-data\runtime.json"
if (-not (Test-Path -LiteralPath $runtime)) { Write-Host "AICompany is not running."; exit 0 }
$value = Get-Content -LiteralPath $runtime -Raw | ConvertFrom-Json
foreach ($processId in @($value.backend_pid, $value.frontend_pid)) {
    if ($processId -is [int] -or $processId -is [long]) { Stop-Process -Id $processId -ErrorAction SilentlyContinue }
}
Remove-Item -LiteralPath $runtime -Force
Write-Host "AICompany local product stopped."
