# Deploy OpenArc to Cloudflare Pages.
#
# Prerequisites (on the machine running this):
#   * Node.js 18+  (provides npx, which downloads wrangler)
#   * Auth — EITHER:  $env:CLOUDFLARE_API_TOKEN = "<token>"
#            OR:      npx wrangler login        (opens a browser)
#   * No credential is ever written to this repo.
#
# Usage:
#   .\scripts\deploy-pages.ps1
#   .\scripts\deploy-pages.ps1 -SiteUrl https://your-domain -Project openarc

param(
    [string]$Project = "openarc",
    [string]$SiteUrl = "https://openarc.example.com",
    [string]$Base = "/"
)

$ErrorActionPreference = "Stop"

# --- Preflight guards -------------------------------------------------------
# Deploying with a placeholder domain would publish wrong canonical / Open Graph
# / sitemap URLs to production, so refuse it outright.
# 1) SHAPE check: https + ASCII host + real TLD (catches placeholders in any
#    language, e.g. https://你的真实域名).
if ($SiteUrl -notmatch '^https://[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}(:\d+)?$') {
    throw "SiteUrl '$SiteUrl' is not a valid https origin (needs ASCII host + TLD). Example: -SiteUrl https://openarc.com"
}
# 2) RESERVED / PLACEHOLDER check: shape alone accepts example.com, which is
#    still a documentation domain and must never reach production.
if ($SiteUrl -match 'example|test|invalid|localhost|your-domain|yourdomain|placeholder') {
    throw "SiteUrl '$SiteUrl' is a reserved or placeholder domain. Pass your real origin, e.g. -SiteUrl https://openarc.com"
}
if (-not (Get-Command npx -ErrorAction SilentlyContinue)) {
    throw "npx not found. Install Node.js 18+ from https://nodejs.org, then re-run."
}

Write-Host "==> build (site=$SiteUrl base=$Base)"
python scripts/build.py --base $Base --site-url $SiteUrl
if ($LASTEXITCODE -ne 0) { throw "build failed" }

Write-Host "==> validate SEO / GEO / a11y"
python scripts/check-seo.py
if ($LASTEXITCODE -ne 0) { throw "validation failed - fix the issues above before deploying" }

Write-Host "==> deploy to Cloudflare Pages"
if (-not $env:CLOUDFLARE_API_TOKEN) {
    Write-Host "CLOUDFLARE_API_TOKEN is not set - falling back to interactive 'wrangler login'."
}
npx --yes wrangler@latest pages deploy dist --project-name $Project
if ($LASTEXITCODE -ne 0) { throw "deploy failed" }

Write-Host "==> done"
