<#
.SYNOPSIS
    Start the codegraph-mcp container with a repo mounted at /repo.

.PARAMETER RepoPath
    Absolute path to the repo you want to work with.
    Defaults to the current working directory.

.EXAMPLE
    .\scripts\start-mcp.ps1 -RepoPath "C:\path\to\your\repo"
    .\scripts\start-mcp.ps1          # mounts $PWD
#>
param(
    [string]$RepoPath = $PWD
)

$RepoPath = (Resolve-Path $RepoPath).Path
$DataDir  = Join-Path $PSScriptRoot "..\data" | Resolve-Path -ErrorAction SilentlyContinue
if (-not $DataDir) {
    $DataDir = New-Item -ItemType Directory -Path (Join-Path $PSScriptRoot "..\data") -Force | Select-Object -ExpandProperty FullName
}

Write-Host "Stopping any existing codegraph-mcp container..."
docker rm -f codegraph-mcp 2>$null

Write-Host "Starting codegraph-mcp with repo: $RepoPath"
docker run -d `
    --name codegraph-mcp `
    -e CODEGRAPH_DB_PATH=/data/codegraph.db `
    -e CODEGRAPH_REPO_ROOT=/repo `
    -e CODEGRAPH_READ_ONLY=false `
    -v "${DataDir}:/data" `
    -v "${RepoPath}:/repo:ro" `
    codegraph-mcp:dev

if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to start container. Have you built the image? Run: docker build -t codegraph-mcp:dev ."
    exit 1
}

Write-Host ""
Write-Host "Container started. In Copilot Chat, call:"
Write-Host "  register_and_index(path='/repo', repo_name='$(Split-Path $RepoPath -Leaf)')"
