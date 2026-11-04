# One-off: rebuilds assets/fonts/ from Google Fonts' own CSS for the exact families, weights and subsets Rithavo used.
#   powershell -File tools/self_host_fonts.ps1 [-Css <saved css file>]
# All three families (Fraunces, Manrope, DM Mono) are SIL Open Font License 1.1 (google/fonts ofl/*/METADATA.pb), which
# permits redistribution and self-hosting with the license text (assets/fonts/LICENSES.txt). Only the latin and latin-ext
# subsets are kept: they cover English, the rupee sign and accented names. Nothing here runs at build time or on the site.
param([string]$Css = "")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $root "assets\fonts"
New-Item -ItemType Directory -Force $out | Out-Null
$ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
$url = "https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600&family=Manrope:wght@400;500;600;700;800&family=DM+Mono:wght@400;500&display=swap"
$css = if ($Css) { [IO.File]::ReadAllText($Css) } else { (Invoke-WebRequest -UseBasicParsing -Uri $url -UserAgent $ua).Content }
$keep = @("latin", "latin-ext")
$files = @{}      # remote url -> local name
$blocks = New-Object System.Collections.Generic.List[string]
foreach ($m in [regex]::Matches($css, '/\* ([a-z\-0-9]+) \*/\s*@font-face \{(.*?)\}', "Singleline")) {
  $subset = $m.Groups[1].Value
  if ($keep -notcontains $subset) { continue }
  $body = $m.Groups[2].Value
  $family = [regex]::Match($body, "font-family: '([^']+)'").Groups[1].Value
  $weight = [regex]::Match($body, "font-weight: ([^;]+);").Groups[1].Value
  $remote = [regex]::Match($body, "url\(([^)]+)\)").Groups[1].Value
  $range = [regex]::Match($body, "unicode-range: ([^;]+);").Groups[1].Value
  $cssWeight = if ($family -eq "DM Mono" -and $weight -eq "500") { "500 600" } else { $weight }   # no 600 face exists: Medium serves it
  $slug = ($family.ToLower() -replace "[^a-z0-9]+", "-")
  $variable = $family -ne "DM Mono"          # Fraunces and Manrope are variable fonts: one file serves every weight
  $name = if ($variable) { "$slug-$subset.woff2" } else { "$slug-$weight-$subset.woff2" }
  if (-not $files.ContainsKey($remote)) { $files[$remote] = $name }
  $blocks.Add("/* $subset */`n@font-face {`n  font-family: '$family';`n  font-style: normal;`n  font-weight: $cssWeight;`n  font-display: swap;`n  src: url(/assets/fonts/$($files[$remote])) format('woff2');`n  unicode-range: $range;`n}")
}
foreach ($kv in $files.GetEnumerator()) {
  $dest = Join-Path $out $kv.Value
  Invoke-WebRequest -UseBasicParsing -Uri $kv.Key -OutFile $dest -UserAgent $ua
  $b = [IO.File]::ReadAllBytes($dest)
  if ($b.Length -lt 1000 -or [Text.Encoding]::ASCII.GetString($b, 0, 4) -ne "wOF2") { throw "not a woff2 file: $($kv.Value)" }
  "{0,-34} {1,8} bytes" -f $kv.Value, $b.Length
}
$header = "/* Self-hosted copies of the Google Fonts Rithavo used before (SIL Open Font License 1.1; see LICENSES.txt).`n   Same families, weights and unicode ranges as the former fonts.googleapis.com stylesheet; latin and latin-ext only.`n   Regenerate with tools/self_host_fonts.ps1. */`n"
[IO.File]::WriteAllText((Join-Path $out "fonts.css"), $header + ($blocks -join "`n`n") + "`n", (New-Object Text.UTF8Encoding($false)))
"fonts.css: $($blocks.Count) @font-face rules, $($files.Count) files"
