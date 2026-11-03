# Ritanya on rithavo.com: integration and activation

Status: **built, tested, switched off.** Nothing here is active in Production. The assistant is a fixed-answer guide:
no model, no provider, no external call, no database access, no cookie.

## What ships on this branch

| Piece | Where |
|---|---|
| Reviewed answers, destinations (relative paths on rithavo.com), page-aware starter questions | `app/ritanya_knowledge.py` |
| Validation, matcher, rate limiter, `POST /api/ritanya/chat`, `GET /api/ritanya/status` | `app/ritanya_chat.py` (mounted from `app/main.py`) |
| Launcher and chat panel (vanilla JS/CSS, same origin) | `assets/ritanya/ritanya.js`, `assets/ritanya/ritanya.css` |
| One script tag right after `<body>` on 19 pages (not sign-in, sign-up, reset-password, onboarding, admin) | `<script src="/assets/ritanya/ritanya.js" defer>` |
| Tests | `tests/test_ritanya_chat.py` (140), `tests/test_ritanya_integration.py`, `tests/e2e/ritanya_browser_check.js` (real Chrome) |

## How it stays off

`RITHAVO_RITANYA_MODE` unset (the Production state) means `mount()` registers **no route**. The script asks
`/api/ritanya/status`, gets a 404, remembers "off" for that tab, and renders nothing. `RITHAVO_RITANYA_PAUSED=true` is the
kill switch: the status route answers `{"enabled": false}` (not cached) and chat answers 503.

## Activation prerequisites (Production is a separate, explicit step)

1. Founder approval to set `RITHAVO_RITANYA_MODE=deterministic` in Production. Also set
   `RITHAVO_RITANYA_IP_HEADER=x-forwarded-for` (Vercel overwrites that header, so a client cannot forge it; verified in the
   Vercel documentation and exercised on a Preview).
2. Privacy wording reviewed and approved (drafted on `launch/privacy-and-wording`; not merged).
3. Vercel plan usage confirmed for one serverless call per message (a status call is cached for 60 s).
4. The portrait stays off. `AVATAR_SRC` in `ritanya.js` is `null`; set it only after the portrait's provenance and usage
   rights are cleared. No portrait or sheet-derived file is in this branch (a test enforces this).
5. Known limit: the rate limit is per server instance, not global. Acceptable for a no-AI assistant; it must be replaced by a
   shared store (a database change, needs approval) before any paid model is ever attached.

## Rollback

Unset `RITHAVO_RITANYA_MODE` (or set `RITHAVO_RITANYA_PAUSED=true`) and redeploy or wait 60 s: the launcher disappears.
