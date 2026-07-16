<#
  sync.ps1 — pull files from the Claude inbox into the right places in this repo.

  THE PROBLEM THIS SOLVES
  Chrome numbers duplicate downloads: insights.html, "insights (1).html",
  "insights (2).html". The plain name is the OLDEST file, not the newest — which
  is exactly backwards from what you want, and it has silently pushed stale code
  more than once. This script ignores the names entirely and picks the NEWEST
  version of each file by write time. It also knows which folder each file
  belongs in, so nothing lands in the root by accident.

  SETUP (one time)
    mkdir C:\Users\danie\claude-inbox
    Chrome > Settings > Downloads > "Ask where to save each file" = ON
    Save everything Claude gives you into claude-inbox. Overwrite when asked.

  USAGE
    .\sync.ps1              # show what would move, then ask before moving
    .\sync.ps1 -Push        # ...and commit + push afterwards
    .\sync.ps1 -DryRun      # just show, change nothing
    .\sync.ps1 -Inbox "D:\somewhere\else"
#>

param(
  [string]$Inbox = "$env:USERPROFILE\claude-inbox",
  [switch]$Push,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

# --- where each file belongs. Add new ones here as the project grows. ---
$Routes = @{
  # scripts/
  "analytics.py"   = "scripts"
  "analyze.py"     = "scripts"
  "apply.py"       = "scripts"
  "autocomplete.py"= "scripts"
  "collect.py"     = "scripts"
  "common.py"      = "scripts"
  "drift.py"       = "scripts"
  "oauth_setup.py" = "scripts"
  "score.py"       = "scripts"
  "script.py"      = "scripts"
  "seo.py"         = "scripts"
  "titles.py"      = "scripts"
  "trends.py"      = "scripts"
  # .github/workflows/
  "daily.yml"      = ".github\workflows"
  # repo root
  "broll_match.py"       = "."
  "broll_viewer.html"    = "."
  "insights.html"        = "."
  "dashboard.html"       = "."
  "dansmrt_editmap.html" = "."
  "transcribe_srt.py"    = "."
  "ROADMAP.md"           = "."
  "POST_PRODUCTION.md"   = "."
  "README.md"            = "."
  "calibration.md"       = "."
  "keywords.txt"         = "."
}

# --- sanity: are we in the repo? ---
if (-not (Test-Path ".git")) {
  Write-Host "Not a git repo. cd into your dansmrt-tool folder first." -ForegroundColor Red
  exit 1
}
if (-not (Test-Path $Inbox)) {
  Write-Host "Inbox not found: $Inbox" -ForegroundColor Red
  Write-Host "Create it with:  mkdir `"$Inbox`"" -ForegroundColor Yellow
  exit 1
}

# --- strip Chrome's " (1)" / " (2)" suffix to recover the true filename ---
function Get-TrueName([string]$name) {
  $ext  = [IO.Path]::GetExtension($name)
  $base = [IO.Path]::GetFileNameWithoutExtension($name)
  $base = $base -replace '\s*\(\d+\)$', ''   # "insights (2)" -> "insights"
  $base = $base -replace '[-_]\d+$', ''      # "daily-6" / "daily_6" -> "daily"
  return "$base$ext"
}

# --- newest wins: group every inbox file by its true name, keep the latest ---
$candidates = @{}
$unknown = @()
Get-ChildItem -Path $Inbox -File -Recurse | ForEach-Object {
  $true_name = Get-TrueName $_.Name
  if (-not $Routes.ContainsKey($true_name)) { $unknown += $_.Name; return }   # unknown file, skip
  if (-not $candidates.ContainsKey($true_name) -or
      $_.LastWriteTime -gt $candidates[$true_name].LastWriteTime) {
    $candidates[$true_name] = $_
  }
}

if ($unknown.Count -gt 0) {
  Write-Host ""
  Write-Host "SKIPPED (not in routing table):" -ForegroundColor DarkYellow
  $unknown | Sort-Object -Unique | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkYellow }
  Write-Host "Add these to `$Routes in sync.ps1 if they belong in the repo." -ForegroundColor DarkGray
}

if ($candidates.Count -eq 0) {
  Write-Host "Nothing in the inbox matches a known project file." -ForegroundColor Yellow
  Write-Host "Inbox: $Inbox"
  exit 0
}

# --- plan ---
Write-Host ""
Write-Host "PLAN" -ForegroundColor Cyan
Write-Host "----" -ForegroundColor Cyan
$plan = @()
foreach ($name in ($candidates.Keys | Sort-Object)) {
  $src  = $candidates[$name]
  $dest = Join-Path $Routes[$name] $name
  $dest = $dest -replace '^\.\\', ''

  $status = "new"
  if (Test-Path $dest) {
    $existing = Get-Item $dest
    if ((Get-FileHash $src.FullName).Hash -eq (Get-FileHash $dest).Hash) {
      $status = "identical"
    } else {
      $status = "replaces"
    }
  }

  $color = switch ($status) { "identical" { "DarkGray" } "new" { "Green" } default { "Yellow" } }
  $note  = if ($src.Name -ne $name) { "  (from '$($src.Name)')" } else { "" }
  Write-Host ("  {0,-9} {1,-24} -> {2}{3}" -f $status, $name, $dest, $note) -ForegroundColor $color
  Write-Host ("            {0}" -f $src.LastWriteTime) -ForegroundColor DarkGray

  if ($status -ne "identical") { $plan += [pscustomobject]@{ Src = $src.FullName; Dest = $dest } }
}

Write-Host ""
if ($plan.Count -eq 0) { Write-Host "Everything already up to date." -ForegroundColor Green; exit 0 }
if ($DryRun)          { Write-Host "-DryRun: nothing changed." -ForegroundColor Cyan; exit 0 }

$answer = Read-Host "Copy $($plan.Count) file(s)? [y/N]"
if ($answer -notmatch '^[Yy]') { Write-Host "Cancelled." -ForegroundColor Yellow; exit 0 }

# --- copy ---
foreach ($p in $plan) {
  $dir = Split-Path $p.Dest -Parent
  if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  Copy-Item $p.Src $p.Dest -Force
  Write-Host "  copied  $($p.Dest)" -ForegroundColor Green
}

# --- optional: ship it ---
if ($Push) {
  Write-Host ""
  Write-Host "Pushing..." -ForegroundColor Cyan
  git pull --no-edit
  git add -A
  $msg = Read-Host "Commit message"
  if (-not $msg) { $msg = "sync: update $($plan.Count) file(s) from claude-inbox" }
  git commit -m $msg
  git push
} else {
  Write-Host ""
  Write-Host "Files copied. To ship:" -ForegroundColor Cyan
  Write-Host "  git pull --no-edit; git add -A; git commit -m 'your message'; git push" -ForegroundColor White
  Write-Host "Or next time run:  .\sync.ps1 -Push" -ForegroundColor DarkGray
}
