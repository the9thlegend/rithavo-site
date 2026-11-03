# Ritanya on rithavo.com: integration and activation

Status: **built, tested on a protected Preview, switched off by default.** Nothing here is active in Production. The assistant is a
fixed-answer guide: no model, no provider, no external call, no database access, no cookie.

## What ships on this branch

| Piece | Where |
|---|---|
| Reviewed answers, destinations (relative paths on rithavo.com), page-aware starter questions | `app/ritanya_knowledge.py` |
| Validation, matcher, limiter, `POST /api/ritanya/chat` (only when enabled), `GET /api/ritanya/status` (always) | `app/ritanya_chat.py` (mounted from `app/main.py`) |
| Launcher and chat panel (vanilla JS/CSS, same origin) | `assets/ritanya/ritanya.js`, `assets/ritanya/ritanya.css` |
| One script tag right after `<body>` on 19 pages (not sign-in, sign-up, reset-password, onboarding, admin) | `<script src="/assets/ritanya/ritanya.js" defer>` |
| Sign-out clearing of the guide's tab storage | `pwa.js` `clearPrivateState` (removes `rv_ritanya_*`) |
| Tests | `tests/test_ritanya_chat.py`, `test_ritanya_integration.py`, `test_ritanya_security.py`, `test_public_wording.py`; real Chrome: `tests/e2e/ritanya_browser_check.js` (local), `ritanya_preview_check.js` (deployed URL) |

## How it stays off, and why that costs almost nothing

`RITHAVO_RITANYA_MODE` unset (the Production state): **no chat route exists** (404), and `/api/ritanya/status` answers
`{"enabled": false}` with `Cache-Control: public, max-age=60, s-maxage=300`. The page script asks status once per tab; the answer
comes from Vercel's edge cache, so a visitor opening a tab does not wake a function. (Before this change the route did not exist
and every new tab cost one uncacheable 404 function run.) No launcher is rendered.

`RITHAVO_RITANYA_PAUSED=true` is the kill switch: status answers `{"enabled": false}` and chat answers 503.

**Environment changes only reach a new deployment.** Switching on, pausing and rolling back all require a redeploy. Vercel's edge
cache is per deployment, so a cached "off" cannot outlive a switch-on and a cached "on" cannot outlive a pause. A tab that already
loaded "on" keeps its launcher until it is closed (it then shows the fallback links, since chat answers 503).

## Activation prerequisites (Production is a separate, explicit step)

1. Founder approval to set, in Production: `RITHAVO_RITANYA_MODE=deterministic` and `RITHAVO_RITANYA_IP_HEADER=x-forwarded-for`,
   then redeploy. The header setting is required: unset, every visitor shares one limiter bucket behind Vercel. Vercel overwrites
   `x-forwarded-for` (measured on a Preview: 35 different client-supplied values did not escape the limit).
2. Privacy wording reviewed and approved (draft, with its review notes, on `launch/privacy-and-wording`; not merged).
3. A decision on the concurrency gap below.
4. The portrait stays off. `AVATAR_SRC` in `ritanya.js` is `null`; set it only after provenance and usage rights are cleared.

## Known limit, measured on Vercel

The limiter keeps its counters per serverless instance. A sequential client is limited at exactly 20 messages per 5 minutes; **60
concurrent requests from one client against a fresh window were all answered** because they were spread across instances. Fixing
that needs a platform rate-limit rule or a shared store, both of which need approval (see the options comparison in the readiness
report). A client address is held in instance memory only until its window has expired and the next purge (about ten minutes
worst case); it is never stored or logged.

## Rollback

Unset `RITHAVO_RITANYA_MODE` (or set `RITHAVO_RITANYA_PAUSED=true`) and redeploy.
