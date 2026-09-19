param([string]$InputDirectory, [string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
$sourceRoot = (Resolve-Path -LiteralPath $InputDirectory).Path
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$outputRoot = (Resolve-Path -LiteralPath $OutputDirectory).Path
$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
try {
    foreach ($file in Get-ChildItem -LiteralPath $sourceRoot -Filter '*.docx') {
        $doc = $word.Documents.Open($file.FullName, $false, $true)
        try {
            $target = Join-Path $outputRoot ($file.BaseName + '.pdf')
            $doc.ExportAsFixedFormat($target, 17)
            Write-Output $target
        } finally {
            $doc.Close(0)
            [System.Runtime.InteropServices.Marshal]::ReleaseComObject($doc) | Out-Null
        }
    }
} finally {
    $word.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null
}
