# Point Telegram at the Worker again with a fresh webhook secret, receiving both messages and
# menu button taps (callback_query). Run from this folder:
#   powershell -ExecutionPolicy Bypass -File .\set_webhook.ps1
# Reads the bot token from ..\.env and the current webhook URL from Telegram; prints no secret.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$line = Get-Content (Join-Path $PSScriptRoot "..\.env") | Where-Object { $_ -match "^\s*TELEGRAM_BOT_TOKEN\s*=" } | Select-Object -First 1
if (-not $line) { throw "TELEGRAM_BOT_TOKEN not found in ..\.env" }
$botToken = ($line -split "=", 2)[1].Trim().Trim('"').Trim("'")
$api = "https://api.telegram.org/bot$botToken"

$url = (Invoke-RestMethod -Uri "$api/getWebhookInfo").result.url
if (-not $url) { throw "The bot has no webhook URL yet - run setup.ps1 first" }

$secret = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 40 | ForEach-Object { [char]$_ })
Write-Host "1/2 Saving the new WEBHOOK_SECRET in the Worker..."
# Through a JSON file, not a pipe: PowerShell adds a line ending to piped text, and a secret
# that ends in "\r" never matches the header Telegram sends (every update then gets 403).
$file = Join-Path $env:TEMP "bindal-webhook-secret.json"
[IO.File]::WriteAllText($file, (@{ WEBHOOK_SECRET = $secret } | ConvertTo-Json -Compress))
try {
    npx.cmd --yes wrangler secret bulk $file | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "wrangler secret bulk failed" }
} finally {
    Remove-Item $file -Force -ErrorAction SilentlyContinue
}

Write-Host "2/2 Pointing Telegram at the Worker (messages + button taps)..."
$hook = Invoke-RestMethod -Method Post -Uri "$api/setWebhook" -ContentType "application/json" -Body (@{
    url = $url; secret_token = $secret; allowed_updates = @("message", "callback_query"); drop_pending_updates = $true
} | ConvertTo-Json)
Write-Host "  setWebhook: $($hook.description)"
Start-Sleep -Seconds 3
$info = (Invoke-RestMethod -Uri "$api/getWebhookInfo").result
Write-Host "  allowed updates: $($info.allowed_updates -join ', ')"
if ($info.last_error_message) { Write-Host "  last error (may be from before this run): $($info.last_error_message)" }
Write-Host "`nDone. Send /menu to the bot and tap a button."
