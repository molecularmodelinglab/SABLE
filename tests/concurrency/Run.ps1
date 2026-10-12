param(
    [string[]]$PytestArgs = @()
)

$ErrorActionPreference = 'Stop'
$project = 'sable-concurrency-' + [guid]::NewGuid().ToString('N').Substring(0, 12)
$compose = Join-Path $PSScriptRoot 'compose.yml'
$exitCode = 2
$previousResults = $env:SABLE_CONCURRENCY_RESULTS
$env:SABLE_CONCURRENCY_RESULTS = Join-Path $PSScriptRoot "results\$project"
New-Item -ItemType Directory -Force $env:SABLE_CONCURRENCY_RESULTS | Out-Null

try {
    & docker compose -p $project -f $compose build tests
    if ($LASTEXITCODE -ne 0) { throw 'Concurrency test image build failed' }
    & docker compose -p $project -f $compose run --rm tests /suite/tests/concurrency --junitxml=/results/junit.xml @PytestArgs
    $exitCode = $LASTEXITCODE
}
finally {
    & docker compose -p $project -f $compose down --volumes --remove-orphans
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Could not clean up test project $project"
        $exitCode = 2
    }
    Write-Output "Reports: $env:SABLE_CONCURRENCY_RESULTS"
    $env:SABLE_CONCURRENCY_RESULTS = $previousResults
}
exit $exitCode
