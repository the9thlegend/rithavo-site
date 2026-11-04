# One-off: replaces the Google Fonts preconnects and stylesheet link in every page with the self-hosted equivalents.
#   powershell -File tools/apply_self_hosted_fonts.ps1
# Idempotent: pages already converted are left alone. Line endings and everything else in each file are preserved.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$pattern = '(?<ind>[ \t]*)<link rel="preconnect" href="https://fonts\.googleapis\.com">(?<eol>\r?\n)[ \t]*<link rel="preconnect" href="https://fonts\.gstatic\.com" crossorigin>\r?\n[ \t]*<link href="https://fonts\.googleapis\.com/css2\?[^"]*" rel="stylesheet">'
$changed = 0
Get-ChildItem -Path $root -Recurse -File -Filter *.html | Where-Object { $_.FullName -notmatch '\\(tests|node_modules|\.git)\\' } | ForEach-Object {
  $text = [IO.File]::ReadAllText($_.FullName)
  if ($text -notmatch 'fonts\.g(oogleapis|static)\.com') { return }
  $new = [regex]::Replace($text, $pattern, {
    param($m)
    $i = $m.Groups["ind"].Value; $e = $m.Groups["eol"].Value
    $i + '<link rel="preload" href="/assets/fonts/manrope-latin.woff2" as="font" type="font/woff2" crossorigin>' + $e +
    $i + '<link rel="preload" href="/assets/fonts/fraunces-latin.woff2" as="font" type="font/woff2" crossorigin>' + $e +
    $i + '<link rel="stylesheet" href="/assets/fonts/fonts.css">'
  })
  if ($new -match 'fonts\.g(oogleapis|static)\.com') { throw "still references Google Fonts: $($_.FullName)" }
  [IO.File]::WriteAllText($_.FullName, $new, (New-Object Text.UTF8Encoding($false)))
  $changed++
}
"pages converted: $changed"
