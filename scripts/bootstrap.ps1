$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

New-Item -ItemType Directory -Force -Path "data/google", "data/web" | Out-Null

if (-not (Test-Path -LiteralPath ".env")) {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    $token = [Convert]::ToHexString($bytes).ToLowerInvariant()
    @"
WEB_PORT=8125
TZ=UTC
GOOGLE_TOKEN=$token
GOOGLE_FIND_HUB_REF=main
RETENTION_DAYS=21
REFRESH_INTERVAL=1800
"@ | Set-Content -LiteralPath ".env" -Encoding ascii
    Write-Host "Created .env with a random Google sidecar token."
} else {
    Write-Host "Existing .env kept unchanged."
}

docker compose pull anisette macless-haystack
docker compose build find-my-web google-provider
docker compose up -d anisette

Write-Host ""
Write-Host "Bootstrap complete."
Write-Host "Next: docker compose run --rm macless-haystack"
Write-Host "Copy Google Auth/secrets.json to data/google/secrets.json."
Write-Host "Finally: docker compose up -d"
