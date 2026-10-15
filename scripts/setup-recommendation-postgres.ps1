param([switch]$Download, [switch]$Stop)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$runtimeRoot = Join-Path $projectRoot '.tools/postgres-7c'
$binRoot = Join-Path $runtimeRoot 'pgsql/bin'
$dataRoot = Join-Path $runtimeRoot 'data'
$port = 55437
# Test-only credentials; loopback-only disposable cluster, never production.
$testUser = 'recommendation_test'
$testPassword = 'local-isolated-7c-only'
$nativeRoot = $runtimeRoot
if ($runtimeRoot -match '[^\x00-\x7F]') {
    # Windows PostgreSQL bootstrap SQL cannot safely encode this workspace's
    # Chinese path under the system ANSI codepage. Binary alias only; the
    # downloaded program remains in .tools. Never repoint an unrelated junction.
    New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
    $nativeRoot = Join-Path ([System.IO.Path]::GetTempPath()) 'anime-postgres-7c-runtime'
    if ($nativeRoot -match '[^\x00-\x7F]') { throw 'An ASCII temporary path is required for Windows PostgreSQL.' }
    if (Test-Path -LiteralPath $nativeRoot) {
        $alias = Get-Item -LiteralPath $nativeRoot -Force
        if ($alias.LinkType -ne 'Junction' -or $alias.Target -ne $runtimeRoot) { throw 'Existing unrelated runtime alias; refusing to repoint it.' }
    } else {
        New-Item -ItemType Junction -Path $nativeRoot -Target $runtimeRoot | Out-Null
    }
    $binRoot = Join-Path $nativeRoot 'pgsql/bin'
    # initdb's directory checks do not reliably traverse junctions either.
    # Only synthetic DB files live in this ASCII TEMP directory; the large
    # downloaded runtime stays in .tools. No existing directory is removed.
    $dataRoot = Join-Path ([System.IO.Path]::GetTempPath()) 'anime-postgres-7c-data'
}
if ($Stop) {
    & (Join-Path $binRoot 'pg_ctl.exe') -D $dataRoot -m fast -w stop
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop the local test instance.' }
    exit
}
if (-not (Test-Path (Join-Path $binRoot 'postgres.exe'))) {
    if (-not $Download) { throw 'Portable PostgreSQL is missing. Review and explicitly pass -Download.' }
    New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
    $archive = Join-Path $runtimeRoot 'postgresql-17.11-5-windows-x64-binaries.zip'
    # Download link verified from PostgreSQL.org -> EDB official binaries page.
    Invoke-WebRequest 'https://get.enterprisedb.com/postgresql/postgresql-17.11-5-windows-x64-binaries.zip' -OutFile $archive
    Get-FileHash -LiteralPath $archive -Algorithm SHA256 | Select-Object Algorithm, Hash
    Expand-Archive -LiteralPath $archive -DestinationPath $runtimeRoot
}
if (-not (Test-Path (Join-Path $dataRoot 'PG_VERSION'))) {
    # Windows initdb requires a real --pwfile ("-" is not stdin). This is
    # generated runtime input, not a production credential or .env edit.
    $passwordFile = Join-Path $nativeRoot 'initdb-password.local'
    if (Test-Path -LiteralPath $passwordFile) { throw 'Unexpected existing initdb password file; inspect it manually.' }
    # Let Windows create the empty directory; native mkdir_p may mistake a
    # junction ancestor for a file while recursively creating its parents.
    New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null
    try {
        [System.IO.File]::WriteAllText($passwordFile, $testPassword, [System.Text.UTF8Encoding]::new($false))
        & (Join-Path $binRoot 'initdb.exe') -D $dataRoot -U $testUser -E UTF8 --locale=C -A scram-sha-256 --pwfile=$passwordFile --no-clean
    } finally {
        # One exact, newly generated transient file only; never recursive.
        if (Test-Path -LiteralPath $passwordFile) { Remove-Item -LiteralPath $passwordFile }
    }
    if ($LASTEXITCODE -ne 0) { throw 'Failed to initialize local isolated PostgreSQL.' }
}
& (Join-Path $binRoot 'pg_ctl.exe') -D $dataRoot status
if ($LASTEXITCODE -ne 0) {
    # pg_ctl is short-lived; launch hidden so no background console appears.
    $ctl = Start-Process -FilePath (Join-Path $binRoot 'pg_ctl.exe') -WindowStyle Hidden -PassThru -ArgumentList @(
        '-D', "`"$dataRoot`"", '-l', "`"$(Join-Path $dataRoot 'postgres.log')`"",
        '-o', "`"-h 127.0.0.1 -p $port`"", '-w', 'start')
    # Start-Process -Wait waits for the entire process tree, including the
    # persistent DB server. Wait for pg_ctl itself only.
    if (-not $ctl.WaitForExit(30000)) { throw 'pg_ctl did not finish within 30 seconds.' }
    if ($ctl.ExitCode -ne 0) { throw "Failed to start PostgreSQL; inspect $dataRoot/postgres.log." }
}
$oldPassword = $env:PGPASSWORD
try {
    $env:PGPASSWORD = $testPassword
    $common = @('-h', '127.0.0.1', '-p', "$port", '-U', $testUser)
    $exists = & (Join-Path $binRoot 'psql.exe') @common -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname = 'recommendation_isolated_tests'"
    if ($LASTEXITCODE -ne 0) { throw 'Could not verify local PostgreSQL ownership/connection.' }
    if ($exists -ne '1') {
        & (Join-Path $binRoot 'createdb.exe') @common recommendation_isolated_tests
        if ($LASTEXITCODE -ne 0) { throw 'Could not create isolated base database.' }
    }
    & (Join-Path $binRoot 'psql.exe') @common -d recommendation_isolated_tests -Atc 'SELECT version(), inet_server_addr(), inet_server_port(), current_database()'
} finally { $env:PGPASSWORD = $oldPassword }
Write-Output 'Ready. Dedicated test database only; no system service installed and no .env changes.'
