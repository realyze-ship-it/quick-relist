param(
    [string]$Python = '.venv\Scripts\python.exe',
    [string]$Destination = 'dist'
)
$ErrorActionPreference = 'Stop'
$repoPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location -LiteralPath $repoPath
$previousPath = $env:PATH
try {
    $pythonPath = (Resolve-Path -LiteralPath $Python).Path
    # Unrelated tools on PATH can supply incompatible DLLs with the same name
    # (for example Poppler's ICU instead of Windows ICU required by Qt).
    $env:PATH = (Split-Path $pythonPath), (Join-Path $env:SystemRoot 'System32'), $env:SystemRoot -join ';'
    & $pythonPath -m PyInstaller --clean --noconfirm --windowed --onedir --noupx `
        --name QuickRelist --distpath $Destination --workpath build\windows `
        --specpath build --paths $repoPath --collect-submodules quick_relist `
        --collect-data selenium --collect-binaries selenium packaging\windows_launcher.pyw
    if ($LASTEXITCODE -ne 0) { throw 'The Windows package did not build.' }
} finally {
    $env:PATH = $previousPath
    Pop-Location
}
