"""Builds assets/fonts/licenses.html (served at /assets/fonts/licenses.html) from assets/fonts/LICENSES.txt.

The SIL Open Font License requires the license text to travel with redistributed fonts. The page carries the same PWA head tags and
script as every other page (tests/test_pwa.py checks that), a single link back to the home page and no Ritanya launcher.
    python tools/build_font_licenses_page.py
"""
import html
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "assets" / "fonts"

text = (FONTS / "LICENSES.txt").read_text(encoding="utf-8")
sections = [s for s in text.split("=== ") if s.strip()]
body = ""
for s in sections:
    name, _, license_text = s.partition(" ===\n")
    body += f"<h2>{html.escape(name)}</h2>\n<pre>{html.escape(license_text.strip())}</pre>\n"

page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Font licenses — Rithavo</title>
<meta name="description" content="Licenses for the open-source fonts Rithavo serves from its own domain.">
<meta name="robots" content="noindex">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<link rel="stylesheet" href="/assets/fonts/fonts.css">
<link rel="stylesheet" href="/styles.css">
<style>
  .licenses {{ max-width: 46rem; margin: 48px auto; padding: 0 20px; }}
  .licenses pre {{ white-space: pre-wrap; font-size: .85rem; margin-bottom: 40px; }}
</style>
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#faf9f6" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#121116" media="(prefers-color-scheme: dark)">
<link rel="apple-touch-icon" href="/assets/pwa/apple-touch-icon.png">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Rithavo">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<link rel="stylesheet" href="/pwa.css">
</head>
<body>
<main class="licenses">
<p><a href="/">&larr; Rithavo</a></p>
<h1>Font licenses</h1>
<p>Rithavo serves copies of three open-source font families from its own domain. Each is licensed under the SIL Open Font License, Version 1.1, reproduced below.</p>
{body}</main>
<script src="/pwa.js" defer></script>
</body></html>
"""
(FONTS / "licenses.html").write_text(page, encoding="utf-8", newline="\n")
print("wrote assets/fonts/licenses.html")
