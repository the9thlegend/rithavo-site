"""
The member-profile page shells (profile redesign), kept as Python strings on purpose: Python modules are always bundled with
the Vercel function, so the pages never depend on a non-Python file being packaged.
  HTML         returned by GET /c/{slug}   (app/routes_public_profile.py) while RITHAVO_PUBLIC_PROFILE_ENABLED is on.
  MANAGE_HTML  returned by GET /c/manage   while RITHAVO_PROFILE_MANAGEMENT_ENABLED is on.
Both are static scaffolds: assets/profile/profile.js and manage.js build the page from the signed-in JSON APIs. No inline
script, no external host; the only inline style attribute is the existing logo's, allowed by hash in the CSP.
"""

_HEAD = """<!DOCTYPE html>
<html lang="en" data-theme-default="dark">
<head>
<meta charset="UTF-8">
<title>@@TITLE@@ | Rithavo</title>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex">
<meta name="theme-color" content="#0a0912">
<link rel="icon" href="/assets/pwa/icon-96.png">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/assets/pwa/apple-touch-icon.png">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Rithavo">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<script src="/assets/profile/theme.js"></script>
<link rel="preload" href="/assets/fonts/manrope-latin.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="/assets/fonts/fonts.css">
<link rel="stylesheet" href="/assets/profile/bearing-tokens.css">
<link rel="stylesheet" href="/assets/profile/profile.css">
<link rel="stylesheet" href="/pwa.css">
</head>
<body class="pf">
<a class="pf-skip" href="#pf-main">@@SKIP@@</a>
@@DEFS@@
<header class="pf-nav">
  <div class="pf-wrap pf-nav__inner">
    @@LOCKUP@@
    <nav class="pf-nav__links" id="pf-links" aria-label="Sections"></nav>
    <div class="pf-nav__actions">
      <div class="pf-theme" role="group" aria-label="Theme"><button type="button" data-theme-choice="light" aria-pressed="false">Light</button><button type="button" data-theme-choice="dark" aria-pressed="true">Dark</button><button type="button" data-theme-choice="system" aria-pressed="false">System</button></div>
      <div class="pf-contents" id="pf-nav-actions"></div>
      <details class="pf-menu"><summary aria-label="Menu">Menu</summary>
        <div class="pf-menu__panel"><div id="pf-menu-links" class="pf-contents"></div><a href="/home/">Rithavo home</a>
          <div class="pf-menu__theme" role="group" aria-label="Theme"><span class="pf-eyebrow">Theme</span><div class="pf-theme"><button type="button" data-theme-choice="light" aria-pressed="false">Light</button><button type="button" data-theme-choice="dark" aria-pressed="true">Dark</button><button type="button" data-theme-choice="system" aria-pressed="false">System</button></div></div></div>
      </details>
    </div>
  </div>
</header>
<main id="pf-main" tabindex="-1" aria-busy="true">
  <div class="pf-wrap" id="pf-root"><div class="pf-skel" aria-hidden="true"><i></i><i></i><i></i></div><p class="pf-help">Loading</p>
    <noscript><p class="pf-help">This page needs JavaScript to show a profile.</p></noscript></div>
</main>
<div class="pf-bar" id="pf-bar" role="region" aria-label="Profile actions"></div>
<div class="pf-toast" id="pf-toast" role="status" aria-live="polite"></div>
<script src="/assets/profile/common.js" defer></script>
<script src="/assets/profile/@@SCRIPT@@" defer></script>
<script src="/pwa.js?v=20261106" defer></script>
</body>
</html>
"""

_DEFS = r"""<svg width="0" height="0" style="position:absolute" aria-hidden="true">
  <defs>
    <linearGradient id="markGradSoft" x1="0" y1="1" x2="1" y2="0">
      <stop offset="0%" stop-color="var(--g1)"/>
      <stop offset="100%" stop-color="var(--g2)"/>
    </linearGradient>
    <linearGradient id="markGrad" x1="0" y1="1" x2="1" y2="0">
      <stop offset="0%" stop-color="var(--g2)"/>
      <stop offset="100%" stop-color="var(--g3)"/>
    </linearGradient>
  </defs>
</svg>"""
_LOCKUP = r"""<a class="wordmark" href="/"><svg class="mark" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5 L12.5 12 L4 19" fill="none" stroke="url(#markGradSoft)" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" opacity="0.65"/><path d="M11 4 L21 12 L11 20" fill="none" stroke="url(#markGrad)" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/></svg><span>Rithavo</span></a>"""


def _page(title: str, skip: str, script: str) -> str:
    return (_HEAD.replace("@@TITLE@@", title).replace("@@SKIP@@", skip).replace("@@SCRIPT@@", script)
            .replace("@@DEFS@@", _DEFS).replace("@@LOCKUP@@", _LOCKUP))


HTML = _page("Rithavo profile", "Skip to profile", "profile.js")
MANAGE_HTML = _page("Public profile", "Skip to settings", "manage.js")
