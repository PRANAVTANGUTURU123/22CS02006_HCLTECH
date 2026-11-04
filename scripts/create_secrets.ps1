# Copies the app's secrets from .env into Google Secret Manager for Cloud Run.
# Prints only secret names, never values. Safe to re-run (adds a new version).
#   powershell -ExecutionPolicy Bypass -File scripts\create_secrets.ps1
$ErrorActionPreference = "Stop"
$gcloud = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
$project = "hcl-hackathon-513621"
$envFile = Join-Path $PSScriptRoot "..\.env"

# Read KEY=value lines from .env.
$values = @{}
foreach ($line in Get-Content $envFile) {
    if ($line -match '^([A-Z_]+)=(.*)$') { $values[$Matches[1]] = $Matches[2].Trim() }
}

# Admin key for uploads: generate once and keep it in .env.
if (-not $values["ADMIN_API_KEY"]) {
    $bytes = New-Object byte[] 24
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $values["ADMIN_API_KEY"] = [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
    Add-Content -Path $envFile -Value "ADMIN_API_KEY=$($values["ADMIN_API_KEY"])" -Encoding utf8
    Write-Host "ADMIN_API_KEY added to .env"
}

$secrets = [ordered]@{
    "groq-api-key"      = $values["GROQ_API_KEY"]
    "gemini-api-key"    = $values["GEMINI_API_KEY"]
    "alert-webhook-url" = $values["ALERT_WEBHOOK_URL"]
    "admin-api-key"     = $values["ADMIN_API_KEY"]
}
$existing = & $gcloud secrets list --project $project --format="value(name)"

foreach ($name in $secrets.Keys) {
    $value = $secrets[$name]
    if (-not $value) { Write-Host "$name : skipped (empty in .env)"; continue }
    $tmp = New-TemporaryFile
    try {
        # Write without a trailing newline so the secret is exactly the value.
        [System.IO.File]::WriteAllText($tmp.FullName, $value)
        if ($existing -contains $name) {
            & $gcloud secrets versions add $name --data-file=$($tmp.FullName) --project $project --quiet | Out-Null
        } else {
            & $gcloud secrets create $name --data-file=$($tmp.FullName) --project $project --replication-policy=automatic --quiet | Out-Null
        }
        Write-Host "$name : ok"
    } finally {
        Remove-Item $tmp.FullName -Force
    }
}
Write-Host "Done. Secrets are in Secret Manager; nothing was printed."
