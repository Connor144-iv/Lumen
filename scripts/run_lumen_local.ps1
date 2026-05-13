$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$venvRoot = Join-Path $repoRoot ".venv"
$venvScripts = Join-Path $venvRoot "Scripts"
$sitePackages = Join-Path $venvRoot "Lib\site-packages"

$defaultClientSecret = Join-Path $repoRoot "backend\secret\client_secret.json"
$legacyClientSecret = Join-Path $repoRoot "backend\secret\google_credentials.json"
$defaultToken = Join-Path $repoRoot "backend\secret\google_token.json"

if (-not $env:LUMEN_GOOGLE_CLIENT_SECRET_PATH) {
    $env:LUMEN_GOOGLE_CLIENT_SECRET_PATH = if (Test-Path $legacyClientSecret) { $legacyClientSecret } else { $defaultClientSecret }
}
if (-not $env:LUMEN_GOOGLE_TOKEN_PATH) {
    $env:LUMEN_GOOGLE_TOKEN_PATH = $defaultToken
}
if (-not $env:LUMEN_GOOGLE_WORKSPACE_ENABLED) {
    $env:LUMEN_GOOGLE_WORKSPACE_ENABLED = if ((Test-Path $env:LUMEN_GOOGLE_CLIENT_SECRET_PATH) -and (Test-Path $env:LUMEN_GOOGLE_TOKEN_PATH)) { "true" } else { "false" }
}

$env:VIRTUAL_ENV = $venvRoot
$env:PATH = "$venvScripts;$env:PATH"
$env:PYTHONPATH = "$repoRoot;$sitePackages" + $(if ($env:PYTHONPATH) { ";$env:PYTHONPATH" } else { "" })

$python = Join-Path $venvScripts "python.exe"
& $python -m uvicorn app:app --reload --host 127.0.0.1 --port 8000
