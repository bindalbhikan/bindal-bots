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
$secret | npx.cmd --yes wrangler secret put WEBHOOK_SECRET | Out-Null
if ($LASTEXITCODE -ne 0) { throw "wrangler secret put WEBHOOK_SECRET failed" }

Write-Host "2/2 Pointing Telegram at the Worker (messages + button taps)..."
$hook = Invoke-RestMethod -Method Post -Uri "$api/setWebhook" -ContentType "application/json" -Body (@{
    url = $url; secret_token = $secret; allowed_updates = @("message", "callback_query")
} | ConvertTo-Json)
Write-Host "  setWebhook: $($hook.description)"
$info = (Invoke-RestMethod -Uri "$api/getWebhookInfo").result
Write-Host "  allowed updates: $($info.allowed_updates -join ', ')"
Write-Host "`nDone. Send /menu to the bot and tap a button."
