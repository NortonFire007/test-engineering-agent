# Host launcher for Windows PowerShell 5.1.
# Builds the harness image and runs the Linux checks with no network and a read-only repo mount.
# Usage: powershell -ExecutionPolicy Bypass -File docker\run-linux-checks.ps1

$scriptDir = $PSScriptRoot
$repoRoot = (Resolve-Path (Join-Path $scriptDir '..')).Path

Push-Location $repoRoot
try {
    docker build -t te-linux-checks -f docker/Dockerfile docker
    if ($LASTEXITCODE -ne 0) {
        Write-Host "docker build failed with exit code $LASTEXITCODE"
        exit $LASTEXITCODE
    }

    docker run --rm --network none -v "${repoRoot}:/work:ro" te-linux-checks
    $runExit = $LASTEXITCODE
    if ($runExit -ne 0) {
        Write-Host "Linux checks failed with exit code $runExit"
    } else {
        Write-Host "Linux checks passed."
    }
    exit $runExit
}
finally {
    Pop-Location
}
