<#
Copies LLM settings from your local .env into Azure App Service app settings, so you never
paste API keys into a terminal command or chat. Blank values in .env are skipped.

Usage (after `az login`, from the project root):
    .\scripts\azure_set_settings.ps1 -ResourceGroup rg-hackathon -AppName <your-app-name>
#>
param(
    [Parameter(Mandatory = $true)] [string] $ResourceGroup,
    [Parameter(Mandatory = $true)] [string] $AppName
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) { throw ".env not found. Copy .env.example to .env first." }

$wanted = @(
    "LLM_PROVIDER", "LLM_FALLBACK_PROVIDER", "LLM_TIMEOUT_SECONDS", "LLM_MAX_RETRIES",
    "LLM_MAX_OUTPUT_TOKENS", "LLM_TEMPERATURE",
    "GROQ_API_KEY", "GROQ_MODEL", "GROQ_REASONING_EFFORT",
    "GEMINI_API_KEY", "GEMINI_MODEL"
)
$values = @{}
foreach ($line in Get-Content $envFile) {
    if ($line -match '^\s*([A-Z_]+)\s*=\s*(.*)\s*$' -and $wanted -contains $Matches[1]) {
        $v = $Matches[2].Trim().Trim('"').Trim("'")
        if ($v) { $values[$Matches[1]] = $v }
    }
}

# Cloud defaults
$values["ENVIRONMENT"] = "azure"
$values["LOG_JSON"] = "true"
$values["DATABASE_URL"] = "sqlite:////home/data/app.db"
$values["SCM_DO_BUILD_DURING_DEPLOYMENT"] = "true"

# Write to a temp JSON file so secrets never appear in the command line or shell history.
$tmp = New-TemporaryFile
try {
    $list = $values.GetEnumerator() | ForEach-Object { @{ name = $_.Key; value = $_.Value; slotSetting = $false } }
    [IO.File]::WriteAllText($tmp.FullName, (ConvertTo-Json @($list)), (New-Object Text.UTF8Encoding $false))
    az webapp config appsettings set -g $ResourceGroup -n $AppName --settings "@$($tmp.FullName)" --output none
    if ($LASTEXITCODE -ne 0) { throw "az webapp config appsettings set failed" }
} finally { Remove-Item $tmp.FullName -Force }

Write-Host "Set these app settings on $AppName (values hidden):"
$values.Keys | Sort-Object | ForEach-Object { Write-Host "  $_" }
