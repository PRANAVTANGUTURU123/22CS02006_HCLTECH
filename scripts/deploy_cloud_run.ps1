# Deploys to Google Cloud Run from source (Google Python buildpacks; no Dockerfile).
# Prerequisites: scripts\create_secrets.ps1 has been run once.
#   powershell -ExecutionPolicy Bypass -File scripts\deploy_cloud_run.ps1
$ErrorActionPreference = "Stop"
$gcloud = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")

# The dashboard is built here and uploaded as frontend/dist (see .gcloudignore).
Push-Location (Join-Path $root "frontend")
npm run build
Pop-Location

Push-Location $root
& $gcloud run deploy cold-chain-alerts `
    --source . `
    --set-build-env-vars GOOGLE_PYTHON_VERSION=3.13 `
    --project hcl-hackathon-513621 `
    --region asia-south1 `
    --allow-unauthenticated `
    --max-instances 1 `
    --memory 512Mi `
    --add-cloudsql-instances hcl-hackathon-513621:asia-south1:hcl-db `
    --set-secrets "DATABASE_URL=database-url:latest,GROQ_API_KEY=groq-api-key:latest,GEMINI_API_KEY=gemini-api-key:latest,ALERT_WEBHOOK_URL=alert-webhook-url:latest,ADMIN_API_KEY=admin-api-key:latest" `
    --set-env-vars "ENVIRONMENT=cloud,LLM_PROVIDER=groq,LLM_FALLBACK_PROVIDER=gemini,GROQ_MODEL=openai/gpt-oss-20b,GROQ_REASONING_EFFORT=low,GEMINI_MODEL=gemini-3.5-flash-lite" `
    --quiet
Pop-Location
