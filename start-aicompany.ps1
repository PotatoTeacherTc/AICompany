$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$automationRoot = Join-Path $projectRoot "Automation"
$webRoot = Join-Path $projectRoot "Web"
$python = Join-Path $automationRoot "venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "AICompany Python environment is unavailable." }

$securePassword = Read-Host "AICompany local login password (12+ characters)" -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
try { $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
if ($plainPassword.Length -lt 12) { throw "The local password must contain at least 12 characters." }

$env:AICOMPANY_LOCAL_EMAIL = "owner@localhost"
$env:AICOMPANY_LOCAL_PASSWORD = $plainPassword
$env:AICOMPANY_SIGNING_SECRET = -join ((1..48) | ForEach-Object { [char](Get-Random -Minimum 33 -Maximum 126) })
$env:AICOMPANY_PRODUCT_ROOT = Join-Path $automationRoot "logs\music-plans"
$env:ALLOW_PAID_PROVIDER = "False"
if (-not $env:AICOMPANY_TEXT_PROVIDER) { $env:AICOMPANY_TEXT_PROVIDER = "fake" }
if (-not $env:AICOMPANY_TEXT_MODEL) { $env:AICOMPANY_TEXT_MODEL = "qwen2.5:1.5b" }
if (-not $env:AICOMPANY_OLLAMA_ENDPOINT) { $env:AICOMPANY_OLLAMA_ENDPOINT = "http://127.0.0.1:11434" }
if (-not $env:AICOMPANY_IMAGE_PROVIDER) { $env:AICOMPANY_IMAGE_PROVIDER = "comfyui" }
if (-not $env:AICOMPANY_IMAGE_MODEL) { $env:AICOMPANY_IMAGE_MODEL = "sd_xl_turbo_1.0_fp16.safetensors" }
if (-not $env:AICOMPANY_COMFYUI_ENDPOINT) { $env:AICOMPANY_COMFYUI_ENDPOINT = "http://127.0.0.1:8188" }
if (-not $env:AICOMPANY_COMFYUI_WORKFLOW_PATH) { $env:AICOMPANY_COMFYUI_WORKFLOW_PATH = Join-Path $automationRoot "workflows\comfyui\checkpoint-basic-v1.json" }
$env:AICOMPANY_VIDEO_PROVIDER = "ffmpeg"
$env:AICOMPANY_NAVER_BLOG_PROVIDER = "playwright"
$env:AICOMPANY_NAVER_PROFILE_DIR = Join-Path $automationRoot ".browser-profiles\naver"
$clientSecret = Join-Path $projectRoot "secrets\client_secret.json"
if (Test-Path -LiteralPath $clientSecret) { $env:AICOMPANY_GOOGLE_CLIENT_SECRET_FILE = $clientSecret }

$backend = Start-Process -FilePath $python -ArgumentList @("-B","-m","uvicorn","application.local_product:create_local_product_app","--factory","--host","127.0.0.1","--port","8000") -WorkingDirectory $automationRoot -WindowStyle Hidden -PassThru
$frontend = Start-Process -FilePath "npm.cmd" -ArgumentList @("run","dev","--","--host","127.0.0.1","--port","5173") -WorkingDirectory $webRoot -WindowStyle Hidden -PassThru
$runtime = Join-Path $automationRoot "product-data\runtime.json"
New-Item -ItemType Directory -Force -Path (Split-Path $runtime) | Out-Null
@{ backend_pid=$backend.Id; frontend_pid=$frontend.Id } | ConvertTo-Json | Set-Content -LiteralPath $runtime -Encoding UTF8
$plainPassword = $null

$deadline = (Get-Date).AddMinutes(2)
do {
    try { if ((Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8000/ready" -TimeoutSec 2).StatusCode -eq 200) { break } } catch { }
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $deadline)
if ((Get-Date) -ge $deadline) { throw "AICompany did not become ready. Run stop-aicompany.ps1 and check local configuration." }
Start-Process "http://127.0.0.1:5173"
Write-Host "AICompany is running. Login: owner@localhost"
Write-Host "Run stop-aicompany.ps1 to stop the local product."
