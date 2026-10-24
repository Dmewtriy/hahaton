# Переключает .env Корсика на локальный ML/RAG (соседний D:\python_projects\hakaton).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) {
    Write-Host "Сначала создайте .env: python -m korsik.cli init --demo"
    exit 1
}
$content = Get-Content $envFile -Raw
$content = $content -replace "(?m)^KORSIK_ML_MODE=.*$", "KORSIK_ML_MODE=http"
$content = $content -replace "(?m)^KORSIK_ML_BASE_URL=.*$", "KORSIK_ML_BASE_URL=http://127.0.0.1:9000"
$content = $content -replace "(?m)^KORSIK_ML_TOKEN=.*$", "KORSIK_ML_TOKEN=local-rag-token"
$content = $content -replace "(?m)^KORSIK_ML_TIMEOUT_SECONDS=.*$", "KORSIK_ML_TIMEOUT_SECONDS=120"
if ($content -notmatch "(?m)^KORSIK_ML_MODE=") { $content += "`nKORSIK_ML_MODE=http`n" }
if ($content -notmatch "(?m)^KORSIK_ML_BASE_URL=") { $content += "KORSIK_ML_BASE_URL=http://127.0.0.1:9000`n" }
if ($content -notmatch "(?m)^KORSIK_ML_TOKEN=") { $content += "KORSIK_ML_TOKEN=local-rag-token`n" }
if ($content -notmatch "(?m)^KORSIK_ML_TIMEOUT_SECONDS=") { $content += "KORSIK_ML_TIMEOUT_SECONDS=120`n" }
Set-Content -Path $envFile -Value $content -Encoding UTF8
Write-Host "Готово. Запустите RAG: conda activate torch-cuda; python -u D:\python_projects\hakaton\scripts\ml_service.py"
Write-Host "Затем перезапустите API и worker Корсика."
