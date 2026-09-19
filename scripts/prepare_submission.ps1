param([switch]$CreateZip)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$submission = Join-Path $root 'submission'
$source = Join-Path $submission '04_Source_Code'
if (Test-Path -LiteralPath $source) {
    $resolved = (Resolve-Path -LiteralPath $source).Path
    if (-not $resolved.StartsWith($submission, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to replace source folder outside submission"
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
New-Item -ItemType Directory -Path $source -Force | Out-Null

$directories = @('.github','src','tests','evaluation','docs','prompts','monitoring','models','scripts','effort')
foreach ($name in $directories) {
    $from = Join-Path $root $name
    if (Test-Path -LiteralPath $from) {
        Copy-Item -LiteralPath $from -Destination (Join-Path $source $name) -Recurse -Force
    }
}
New-Item -ItemType Directory -Path (Join-Path $source 'Capstone_Pack/05_Datasets') -Force | Out-Null
foreach ($name in @('development_tickets.json','validation_tickets.json','documentation.json','ground_truth_responses.json')) {
    Copy-Item -LiteralPath (Join-Path $root "Capstone_Pack/05_Datasets/$name") -Destination (Join-Path $source "Capstone_Pack/05_Datasets/$name")
}
foreach ($name in @('README.md','requirements.txt','.env.example','.gitignore','LICENSE')) {
    $from = Join-Path $root $name
    if (Test-Path -LiteralPath $from) { Copy-Item -LiteralPath $from -Destination (Join-Path $source $name) }
}

# Remove generated caches and local state from the copied snapshot only.
Get-ChildItem -LiteralPath $source -Directory -Recurse -Force |
    Where-Object { $_.Name -in @('__pycache__','.pytest_cache','chroma') } |
    Sort-Object FullName -Descending |
    Remove-Item -Recurse -Force
Get-ChildItem -LiteralPath $source -File -Recurse -Force |
    Where-Object { $_.Name -in @('.env','.DS_Store') -or $_.Extension -in @('.pyc','.db') } |
    Remove-Item -Force

# Preserve the complete, genuine Git history without exposing the working
# repository's .git directory as loose internal files in the submission tree.
$bundle = Join-Path $source 'FDE_Capstone_Complete.bundle'
git -C $root bundle create $bundle --all
if ($LASTEXITCODE -ne 0) { throw "Could not create the Git history bundle" }
git -C $root bundle verify $bundle
if ($LASTEXITCODE -ne 0) { throw "Git history bundle verification failed" }

if ($CreateZip) {
    $video = Join-Path $submission '01_Video/ShashidharBS_Capstone_Video.mp4'
    if (-not (Test-Path -LiteralPath $video)) {
        throw "Add 01_Video/ShashidharBS_Capstone_Video.mp4 before creating the final ZIP."
    }
    $zip = Join-Path $root 'ShashidharBS_Capstone_Submission.zip'
    if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
    Compress-Archive -Path (Join-Path $submission '*') -DestinationPath $zip -CompressionLevel Optimal
    Write-Output $zip
} else {
    Write-Output $source
}
