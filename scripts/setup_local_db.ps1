<#
Creates the local PostgreSQL user and database described by DATABASE_URL in .env.

- The app user gets only what it needs (LOGIN, owns its own database). It is NOT a superuser
  and can't create roles or other databases (least privilege).
- psql will prompt for the password of the PostgreSQL admin user "postgres" (the one you chose
  when installing PostgreSQL). Type it in the terminal, never into chat or a file.
- Safe to run more than once: existing user/database are kept (the password is re-synced to .env).

Usage (from the project root):
    .\scripts\setup_local_db.ps1
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) { throw ".env not found. Copy .env.example to .env first." }

$line = Get-Content $envFile | Where-Object { $_ -match '^\s*DATABASE_URL\s*=' } | Select-Object -First 1
if (-not $line) { throw "DATABASE_URL is not set in .env" }
$url = ($line -split '=', 2)[1].Trim()
if ($url -notmatch '^postgresql(\+psycopg)?://([A-Za-z0-9_]+):([A-Za-z0-9]+)@([A-Za-z0-9.\-]+):(\d+)/([A-Za-z0-9_]+)$') {
    throw "DATABASE_URL must look like postgresql+psycopg://user:password@host:port/dbname (letters/digits only)"
}
$user, $pw, $dbHost, $port, $db = $Matches[2], $Matches[3], $Matches[4], $Matches[5], $Matches[6]
if ($pw -eq "CHANGE_ME") { throw "DATABASE_URL in .env still has the CHANGE_ME placeholder password." }

$psql = Get-ChildItem "C:\Program Files\PostgreSQL\*\bin\psql.exe" -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Select-Object -First 1
if (-not $psql) { throw "psql.exe not found under C:\Program Files\PostgreSQL. Is PostgreSQL installed?" }

$sql = @"
DO `$`$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$user') THEN
    CREATE ROLE $user LOGIN PASSWORD '$pw' NOSUPERUSER NOCREATEDB NOCREATEROLE;
  ELSE
    ALTER ROLE $user WITH LOGIN PASSWORD '$pw' NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;
END
`$`$;
SELECT 'CREATE DATABASE $db OWNER $user'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$db')\gexec
REVOKE ALL ON DATABASE $db FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE $db TO $user;
"@

# The SQL contains the app password, so write it to a temp file and always delete it.
$tmp = New-TemporaryFile
try {
    [IO.File]::WriteAllText($tmp.FullName, $sql, (New-Object Text.UTF8Encoding $false))
    Write-Host "Connecting to PostgreSQL at ${dbHost}:$port as admin user 'postgres'..."
    Write-Host "Enter the 'postgres' password you chose when installing PostgreSQL:"
    & $psql.FullName -h $dbHost -p $port -U postgres -d postgres -v ON_ERROR_STOP=1 -q -f $tmp.FullName
    if ($LASTEXITCODE -ne 0) { throw "psql failed (exit $LASTEXITCODE). Wrong admin password?" }
} finally {
    Remove-Item $tmp.FullName -Force -ErrorAction SilentlyContinue
}
Write-Host "Done: user '$user' and database '$db' are ready (password stored only in .env)."
