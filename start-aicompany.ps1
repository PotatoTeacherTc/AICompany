[CmdletBinding()]
param([switch]$ResetOwnerPassword)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$automationRoot = Join-Path $projectRoot "Automation"
$webRoot = Join-Path $projectRoot "Web"
$python = Join-Path $automationRoot "venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "AICompany Python environment is unavailable." }
$modelsRoot = Join-Path $projectRoot "Models"
$cacheRoot = Join-Path $projectRoot "Cache"
$logsRoot = Join-Path $projectRoot "Logs\LocalProduct"
$artifactsRoot = Join-Path $projectRoot "Artifacts"
$profilesRoot = Join-Path $projectRoot "BrowserProfiles"
$tempRoot = Join-Path $projectRoot "Temp"
foreach ($path in @($modelsRoot, $cacheRoot, $logsRoot, $artifactsRoot, $profilesRoot, $tempRoot)) {
    New-Item -ItemType Directory -Force -Path $path | Out-Null
}
$runtime = Join-Path $logsRoot "runtime.json"
if (Test-Path -LiteralPath $runtime) {
    try {
        $existingRuntime = Get-Content -LiteralPath $runtime -Raw | ConvertFrom-Json
        $running = @($existingRuntime.backend_pid, $existingRuntime.frontend_pid) | Where-Object {
            Get-Process -Id $_ -ErrorAction SilentlyContinue
        }
        if ($running) { throw "AICompany is already running. Run stop-aicompany.ps1 first." }
    } catch {
        if ($_.Exception.Message -like "AICompany is already running*") { throw }
    }
    Remove-Item -LiteralPath $runtime -Force
}
try {
    $occupied = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8000/ready" -TimeoutSec 2
    if ($occupied.StatusCode) { throw "Port 8000 is already serving another application. Stop it before starting AICompany." }
} catch {
    if ($_.Exception.Message -like "Port 8000 is already serving*") { throw }
}

$passwordPrompt = if ($ResetOwnerPassword) { "New AICompany local owner password (12+ characters)" } else { "AICompany local login password (12+ characters)" }
$securePassword = Read-Host $passwordPrompt -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
try { $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
if ($plainPassword.Length -lt 12) { throw "The local password must contain at least 12 characters." }

$env:AICOMPANY_LOCAL_EMAIL = "owner@localhost"
$env:AICOMPANY_LOCAL_PASSWORD = $plainPassword
$env:AICOMPANY_SIGNING_SECRET = -join ((1..48) | ForEach-Object { [char](Get-Random -Minimum 33 -Maximum 126) })
$env:AICOMPANY_PRODUCT_ROOT = $logsRoot
$env:AICOMPANY_ARTIFACT_ROOT = $artifactsRoot
$env:AICOMPANY_RUNTIME_FILE = $runtime
$env:AICOMPANY_TEMP_ROOT = $tempRoot
$env:TEMP = $tempRoot
$env:TMP = $tempRoot
$env:NPM_CONFIG_CACHE = Join-Path $cacheRoot "npm"
$env:UV_CACHE_DIR = Join-Path $cacheRoot "uv"
$env:PIP_CACHE_DIR = Join-Path $cacheRoot "pip"
$env:OLLAMA_MODELS = Join-Path $modelsRoot "Ollama"
$env:ALLOW_PAID_PROVIDER = "False"
if ($ResetOwnerPassword) {
    $env:AICOMPANY_RESET_OWNER_PASSWORD = "true"
    $resetExitCode = 1
    Push-Location $automationRoot
    try {
        & $python -B -m application.local_product_reset
        $resetExitCode = $LASTEXITCODE
    } finally {
        Pop-Location
        $env:AICOMPANY_RESET_OWNER_PASSWORD = $null
    }
    if ($resetExitCode -ne 0) {
        $env:AICOMPANY_LOCAL_PASSWORD = $null
        $env:AICOMPANY_SIGNING_SECRET = $null
        throw "AICompany local owner password reset failed."
    }
}
if (-not $env:AICOMPANY_TEXT_PROVIDER) { $env:AICOMPANY_TEXT_PROVIDER = "fake" }
if (-not $env:AICOMPANY_TEXT_MODEL) { $env:AICOMPANY_TEXT_MODEL = "qwen2.5:1.5b" }
if (-not $env:AICOMPANY_OLLAMA_ENDPOINT) { $env:AICOMPANY_OLLAMA_ENDPOINT = "http://127.0.0.1:11434" }
if (-not $env:AICOMPANY_IMAGE_PROVIDER) { $env:AICOMPANY_IMAGE_PROVIDER = "comfyui" }
if (-not $env:AICOMPANY_IMAGE_MODEL) { $env:AICOMPANY_IMAGE_MODEL = "sd_xl_turbo_1.0_fp16.safetensors" }
if (-not $env:AICOMPANY_COMFYUI_ENDPOINT) { $env:AICOMPANY_COMFYUI_ENDPOINT = "http://127.0.0.1:8188" }
if (-not $env:AICOMPANY_COMFYUI_WORKFLOW_PATH) { $env:AICOMPANY_COMFYUI_WORKFLOW_PATH = Join-Path $automationRoot "workflows\comfyui\checkpoint-basic-v1.json" }
$env:AICOMPANY_VIDEO_PROVIDER = "ffmpeg"
$env:AICOMPANY_NAVER_BLOG_PROVIDER = "playwright"
$env:AICOMPANY_NAVER_PROFILE_DIR = Join-Path $profilesRoot "Naver"
$clientSecret = Join-Path $projectRoot "secrets\client_secret.json"
if (Test-Path -LiteralPath $clientSecret) { $env:AICOMPANY_GOOGLE_CLIENT_SECRET_FILE = $clientSecret }

$backend = $null
$frontend = $null
try {
    $backend = Start-Process -FilePath $python -ArgumentList @("-B","-m","uvicorn","application.local_product:create_local_product_app","--factory","--host","127.0.0.1","--port","8000") -WorkingDirectory $automationRoot -WindowStyle Hidden -PassThru
    $deadline = (Get-Date).AddMinutes(2)
    $ready = $false
    do {
        if ($backend.HasExited) { throw "AICompany Backend stopped during startup." }
        try {
            if ((Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8000/ready" -TimeoutSec 2).StatusCode -eq 200) { $ready = $true; break }
        } catch { }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    if (-not $ready) { throw "AICompany did not become ready." }

    $loginBody = @{ email=$env:AICOMPANY_LOCAL_EMAIL; password=$plainPassword } | ConvertTo-Json
    try {
        $loginResult = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/auth/login" -ContentType "application/json" -Body $loginBody -TimeoutSec 15
    } catch {
        throw "AICompany local owner login verification failed. On later runs, enter the original owner password."
    }
    if (-not $loginResult.access_token) { throw "AICompany local owner login verification failed." }
    $loginResult = $null
    $loginBody = $null

    $frontend = Start-Process -FilePath "npm.cmd" -ArgumentList @("run","dev","--","--host","127.0.0.1","--port","5173") -WorkingDirectory $webRoot -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 1
    if ($frontend.HasExited) { throw "AICompany Frontend stopped during startup." }
    New-Item -ItemType Directory -Force -Path (Split-Path $runtime) | Out-Null
    @{ backend_pid=$backend.Id; frontend_pid=$frontend.Id } | ConvertTo-Json | Set-Content -LiteralPath $runtime -Encoding UTF8
    Start-Process "http://127.0.0.1:5173"
    Write-Host "AICompany is running and local login was verified. Login: owner@localhost"
    Write-Host "Run stop-aicompany.ps1 to stop the local product."
} catch {
    if ($frontend -and -not $frontend.HasExited) { Stop-Process -Id $frontend.Id -ErrorAction SilentlyContinue }
    if ($backend -and -not $backend.HasExited) { Stop-Process -Id $backend.Id -ErrorAction SilentlyContinue }
    throw
} finally {
    $plainPassword = $null
    $loginBody = $null
    $loginResult = $null
    $env:AICOMPANY_LOCAL_PASSWORD = $null
    $env:AICOMPANY_SIGNING_SECRET = $null
    $env:AICOMPANY_RESET_OWNER_PASSWORD = $null
}
