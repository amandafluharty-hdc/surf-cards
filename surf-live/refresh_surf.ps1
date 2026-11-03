# refresh_surf.ps1 -- build the surf cards on THIS computer (Surfline allows it) and push them to GitHub Pages.
# Usage:  powershell -ExecutionPolicy Bypass -File refresh_surf.ps1 [-Repo "C:\path\to\surf-cards"]
# Optional: set a Windows user environment variable CARTO_BASEMAP_KEY (same value as the old GitHub secret).

param([string]$Repo = "$env:USERPROFILE\surf-cards", [string]$Python = "")

$ErrorActionPreference = "Continue"
# Task Scheduler does not load Anaconda's PATH, so look for Anaconda's python.exe first.
if (-not $Python) {
    $ana = Join-Path $env:USERPROFILE "anaconda3\python.exe"
    if (Test-Path $ana) { $Python = $ana } else { $Python = "python" }
}
$env:PYTHONIOENCODING = "utf-8"
Set-Location $Repo
$log = Join-Path $Repo "refresh_surf.log"
function Log($m) { "$(Get-Date -Format s)  $m" | Tee-Object -FilePath $log -Append }

# keep the log small
if ((Test-Path $log) -and ((Get-Item $log).Length -gt 500KB)) { Get-Content $log -Tail 300 | Set-Content "$log.tmp"; Move-Item -Force "$log.tmp" $log }

Log "---- run start"
git pull --rebase --autostash 2>&1 | ForEach-Object { Log $_ }
if ($LASTEXITCODE -ne 0) { Log "git pull failed - stopping"; exit 1 }

& $Python surf-live\scripts\build_surf.py 2>&1 | ForEach-Object { Log $_ }
if ($LASTEXITCODE -ne 0) { Log "build_surf.py failed - stopping (site keeps last good version)"; exit 1 }

# same safety check the workflow had: don't publish empty cards
$spots = Get-Content surf-live\surf.json -Raw -Encoding UTF8 | ConvertFrom-Json
$ok = @($spots | Where-Object { $_.rating }).Count
Log "$ok of $($spots.Count) spots have a rating"
if ($ok -lt 3) { Log "Too few ratings - NOT publishing (site keeps last good version)"; exit 1 }

New-Item -ItemType Directory -Force -Path surf-live\site\map | Out-Null
Copy-Item surf-live\kauai_surf_cards.html surf-live\site\index.html -Force
Copy-Item surf-live\surf.json surf-live\site\surf.json -Force
Copy-Item surf-live\kauai_surf_map.html surf-live\site\map\index.html -Force

git add surf-live/site
git diff --cached --quiet
if ($LASTEXITCODE -eq 0) { Log "No changes to publish"; exit 0 }

git commit -m "Refresh surf cards $(Get-Date -Format s)" 2>&1 | ForEach-Object { Log $_ }
git push 2>&1 | ForEach-Object { Log $_ }
if ($LASTEXITCODE -ne 0) { Log "git push failed"; exit 1 }
Log "Published"
