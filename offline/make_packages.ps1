# Builds the two offline packages from the WSL distribution of the online PC:
#
#   <OutDir>\embedding\Scopus-Embedding-Ubuntu24.tar   Ubuntu + Docker + embedding app (~/ScopusEmbedding)
#   <OutDir>\search\Scopus-Search-Ubuntu24.tar         Ubuntu + Docker + search app (~/Scopus)
#
# The distribution is exported once; each package is made from a temporary copy from which
# the other app (files, Docker images, volumes) is removed and in which the remaining app is
# started and checked.  The distribution itself is not changed.  Run in Windows PowerShell:
#
#   powershell -ExecutionPolicy Bypass -File offline\make_packages.ps1 -OutDir E:\Scopus-Offline
#
# Needs free space: about the size of the distribution twice on OutDir's drive (the full
# export is deleted at the end) and once on TempDir's drive.  Takes about 1-2 hours.
# Stop both apps first (docker compose down in ~/Scopus and ~/ScopusEmbedding).
param(
    [string]$Distro = 'Ubuntu-24.04',
    [string]$OutDir = 'E:\Scopus-Offline',
    [string]$TempDir = 'D:\WSL\_scopus_tmp',
    [string]$User = 'adonis'
)
$ErrorActionPreference = 'Stop'
$env:WSL_UTF8 = '1'
$here = $PSScriptRoot
$hereWsl = '/mnt/' + $here.Substring(0, 1).ToLower() + ($here.Substring(2) -replace '\\', '/')
$log = Join-Path $OutDir 'make_packages.log'
$full = Join-Path $OutDir '_full.tar'
$embTar = Join-Path $OutDir 'embedding\Scopus-Embedding-Ubuntu24.tar'
$seaTar = Join-Path $OutDir 'search\Scopus-Search-Ubuntu24.tar'

New-Item -ItemType Directory -Force (Join-Path $OutDir 'embedding'), (Join-Path $OutDir 'search'), $TempDir | Out-Null
[System.IO.File]::WriteAllText($log, "")

function Append([string]$text) {
    Write-Host $text
    for ($i = 0; $i -lt 50; $i++) {      # a reader may hold the file for a moment
        try { [System.IO.File]::AppendAllText($log, $text + "`r`n"); return } catch { Start-Sleep -Milliseconds 200 }
    }
}
function Log([string]$m) { Append ("{0}  {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) }
function Run([string[]]$wslArgs) {
    $t = Get-Date
    $o = & wsl.exe @wslArgs 2>&1 | Out-String
    $code = $LASTEXITCODE
    if ($o.Trim()) { Append $o.TrimEnd() }
    if ($code -ne 0) { throw "wsl $($wslArgs -join ' ') failed with exit code $code" }
    Log ("  done in {0:N0} s" -f ((Get-Date) - $t).TotalSeconds)
}
function Unregister([string]$name) {
    if (-not $name.StartsWith('ScopusTmp')) { throw "refusing to unregister $name" }
    Run @('--unregister', $name)
}
function SizeGB([string]$path) { "{0:N1} GB" -f ((Get-Item $path).Length / 1GB) }

Add-Type -Name P -Namespace KeepAwake -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint f);'
[KeepAwake.P]::SetThreadExecutionState([uint32]"0x80000001") | Out-Null    # no sleep while this runs
try {
    foreach ($n in 'ScopusTmpEmbedding', 'ScopusTmpSearch') {
        if ((wsl.exe -l -q | Out-String) -match "(?m)^\s*$n\s*$") { Unregister $n }
    }
    Log "1/9 wsl --shutdown"
    Run @('--shutdown')
    Log "2/9 export $Distro -> $full"
    Run @('--export', $Distro, $full)
    Log "  full export: $(SizeGB $full)"

    Log "3/9 temporary copy for the embedding package"
    Run @('--import', 'ScopusTmpEmbedding', (Join-Path $TempDir 'embedding'), $full, '--version', '2')
    Log "4/9 strip it to the embedding app and check it"
    Run @('-d', 'ScopusTmpEmbedding', '-u', 'root', '--', 'bash', "$hereWsl/strip_to_embedding.sh", $User)
    Run @('--terminate', 'ScopusTmpEmbedding')
    Log "5/9 export the embedding package -> $embTar"
    Run @('--export', 'ScopusTmpEmbedding', $embTar)
    Log "  embedding package: $(SizeGB $embTar)"
    Unregister 'ScopusTmpEmbedding'

    Log "6/9 temporary copy for the search package"
    Run @('--import', 'ScopusTmpSearch', (Join-Path $TempDir 'search'), $full, '--version', '2')
    Remove-Item $full
    Log "7/9 strip it to the search app and check it"
    Run @('-d', 'ScopusTmpSearch', '-u', 'root', '--', 'bash', "$hereWsl/strip_to_search.sh", $User)
    Run @('--terminate', 'ScopusTmpSearch')
    Log "8/9 export the search package -> $seaTar"
    Run @('--export', 'ScopusTmpSearch', $seaTar)
    Log "  search package: $(SizeGB $seaTar)"
    Unregister 'ScopusTmpSearch'
    Remove-Item -Recurse -Force $TempDir

    Log "9/9 SHA-256 checksums and guides"
    foreach ($f in $embTar, $seaTar) {
        $h = (Get-FileHash -Algorithm SHA256 $f).Hash.ToLower()
        Set-Content -Path "$f.sha256" -Value "$h  $(Split-Path $f -Leaf)" -Encoding ascii
        Log "  $h  $(Split-Path $f -Leaf)"
    }
    Copy-Item (Join-Path $here 'PACKAGES.md') (Join-Path $OutDir 'README.md') -Force
    Copy-Item (Join-Path $here 'START-OFFLINE-embedding.md') (Join-Path $OutDir 'embedding\START-OFFLINE.md') -Force
    Copy-Item (Join-Path $here 'START-OFFLINE-search.md') (Join-Path $OutDir 'search\START-OFFLINE.md') -Force
    Log "starting $Distro again"
    Run @('-d', $Distro, '--', 'true')
    Log "FINISHED"
}
catch {
    Log "ERROR: $_"
    throw
}
