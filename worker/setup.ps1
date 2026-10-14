# One-time setup: deploy the Worker, store its secrets, point the Telegram bot at it.
# Run in your own PowerShell window from this folder:  .\setup.ps1
# Secrets are read from the project .env (bot token, chat id) and typed GitHub token;
# nothing is printed to the screen.
# -FromClipboard reads the GitHub token from the clipboard (then clears it) instead of prompting.

param([switch]$FromClipboard)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Read-EnvValue($file, $key) {
    $line = Get-Content $file | Where-Object { $_ -match "^\s*$key\s*=" } | Select-Object -First 1
    if (-not $line) { throw "$key not found in $file" }
    ($line -split "=", 2)[1].Trim().Trim('"').Trim("'")
}

function Put-Secret($name, $value) {
    $value | npx.cmd --yes wrangler secret put $name | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "wrangler secret put $name failed" }
    Write-Host "  secret $name saved"
}

$envFile  = Join-Path $PSScriptRoot "..\.env"
$botToken = Read-EnvValue $envFile "TELEGRAM_BOT_TOKEN"
$chatId   = Read-EnvValue $envFile "TELEGRAM_CHAT_ID"

Write-Host "1/4 Cloudflare login (browser will open if needed)..."
$who = npx.cmd --yes wrangler whoami 2>&1 | Out-String
if ($LASTEXITCODE -ne 0 -or $who -match "not authenticated") { npx.cmd --yes wrangler login }

Write-Host "2/4 Deploying Worker..."
$deploy = npx.cmd --yes wrangler deploy 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) { Write-Host $deploy; throw "deploy failed" }
$url = [regex]::Match($deploy, "https://[\w.-]+\.workers\.dev").Value
if (-not $url) { Write-Host $deploy; throw "could not find workers.dev URL in deploy output" }
Write-Host "  deployed: $url"

Write-Host "3/4 Saving secrets..."
if ($FromClipboard) {
    $ghToken = (Get-Clipboard -Raw).Trim()
    if ($ghToken -notmatch '^(github_pat_|ghp_)\w+$') { throw "Clipboard does not hold a GitHub token - copy it and run again" }
    Set-Clipboard -Value " "
} else {
    $secure = Read-Host "Paste the GitHub fine-grained token (Actions: read & write on bindal-bots)" -AsSecureString
    $ghToken = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
}
$webhookSecret = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 40 | ForEach-Object { [char]$_ })

Put-Secret "TELEGRAM_BOT_TOKEN" $botToken
Put-Secret "GITHUB_TOKEN" $ghToken
Put-Secret "WEBHOOK_SECRET" $webhookSecret
Put-Secret "ALLOWED_CHAT_IDS" $chatId

Write-Host "4/4 Connecting Telegram bot..."
$api = "https://api.telegram.org/bot$botToken"
$hook = Invoke-RestMethod -Method Post -Uri "$api/setWebhook" -ContentType "application/json" -Body (@{
    url = $url; secret_token = $webhookSecret; allowed_updates = @("message", "callback_query"); drop_pending_updates = $true
} | ConvertTo-Json)
Write-Host "  setWebhook: $($hook.description)"

$commands = @(
    @{ command = "menu";    description = "Buttons for every report" },
    @{ command = "sale";    description = "Daily sale report (optional: from to dates)" },
    @{ command = "vms";     description = "Myntra VMS report (optional: from to dates)" },
    @{ command = "ajiotat"; description = "AJIO TAT update (optional: days)" },
    @{ command = "ajiospf"; description = "AJIO SPF claims (optional: dry)" },
    @{ command = "salepic"; description = "Sale sheet picture (optional: YYYY-MM)" },
    @{ command = "vmspic";  description = "VMS sheet picture (optional: YYYY-MM)" },
    @{ command = "status";  description = "Last run of each report" },
    @{ command = "help";    description = "Show commands" }
)
Invoke-RestMethod -Method Post -Uri "$api/setMyCommands" -ContentType "application/json" -Body (@{ commands = $commands } | ConvertTo-Json -Depth 4) | Out-Null
Write-Host "  command menu set"

Write-Host "`nDone. Send /help to the bot."
