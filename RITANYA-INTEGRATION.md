# Ritanya on rithavo.com: integration and activation

Status: **built, tested on protected Previews, switched off by default.** Nothing here is active in Production. The assistant is a
fixed-answer guide: no model, no provider, no external call, no database access, no cookie. Two engines answer identically; a
setting picks which one runs.

## The two engines (`RITHAVO_RITANYA_MODE`)

| Value | What runs | Messages | Limiter, addresses, logs |
|---|---|---|---|
| unset / anything else | nothing; the page shows no guide | none | none |
| `browser` | the **browser engine**, `assets/ritanya/engine.js`, inside the page | **never leave the page**: zero network requests per message | none: there is no chat route |
| `deterministic` | the **server engine**, `POST /api/ritanya/chat` | sent to the function | per-instance limiter (needs `RITHAVO_RITANYA_IP_HEADER=x-forwarded-for`); log line has request id, outcome, topic label, duration |

`RITHAVO_RITANYA_PAUSED=true` turns either engine off. **Environment changes only reach a new deployment**, so switching on, switching
engines, pausing and rolling back each need a redeploy.

### Single source of truth
The Python matcher (`app/ritanya_chat.py`, `app/ritanya_knowledge.py`) is the reference. `assets/ritanya/engine.js` is **generated** from
it by `tools/build_ritanya_engine.py` (every topic, destination, text and matching pattern is read from the Python modules; only the order
of the checks is written by hand). After changing any answer or pattern, run `python tools/build_ritanya_engine.py` and
`REGEN_RITANYA_FIXTURE=1 pytest tests/test_ritanya_engine_parity.py`; the tests fail until you do.

`tests/test_ritanya_engine_parity.py` proves the two engines return exactly the same reply, topic, links, suggestions and refusal/clarify/
unknown flags over a corpus of about 19,000 cases (every topic, every follow-up, hostile and obfuscated input, unsupported questions,
validation edge cases, seeded fuzz), and `tests/e2e/ritanya_engine_check.js` repeats the key cases inside a real browser.

## The kill switch
The status route (`/api/ritanya/status`) always exists and is edge-cached. Off or paused it answers `{"enabled": false}`. The server is
authoritative; the page follows this policy (`RECHECK_MS` = 2 minutes, `GRACE_MS` = 5 minutes in `assets/ritanya/ritanya.js`):

| Situation | What the page does |
|---|---|
| Last successful answer is under 2 minutes old | trusts it; no request |
| Older, and the server answers **on** | carries on; the clock restarts |
| Older, and the server answers **off** / **paused** (or the route is gone, 404) | removes the whole guide **at once** and clears the conversation, however recent the last "on" was |
| Older, and the server **cannot be asked** (offline, refused, timeout after 5 s, a 5xx, a non-JSON page such as a sign-in portal) | keeps working on the last known "on" **until 5 minutes after the last successful answer**; a failed attempt never restarts that clock |
| 5 minutes pass with no successful answer | removes the guide, clears the conversation and the stored status; also happens by itself on an idle page (a timer armed while the server is unreachable) |
| After such an outage removal, the server later answers **on** (the browser comes back online, or the tab becomes visible) | the guide returns on the same page, with a new empty conversation |
| After removal, the server answers **off** | it does not return |

The page asks when the panel opens, when the tab becomes visible, before each message, and (only during an outage) when the grace period
is about to end. So after a redeploy with the guide paused or off, every open page drops it within about 2 minutes of its next use plus
the edge cache (60 s; 5 minutes for the cached "off"), and no page loads it afterwards. `tests/e2e/ritanya_killswitch_check.js` runs this
whole table in real Chrome against a deployed Preview.

## Older browsers
`engine.js` and `ritanya.js` use no regex look-behind and nothing newer than 2017 (a script containing look-behind is not parsed at all
by a browser older than about 2023, so the whole engine would fail). `tests/test_ritanya_engine_compat.py` enforces this and proves the
text normalisation is identical to the look-behind form it replaced over about 111,000 enumerated strings. If the engine cannot be
loaded or run for any reason (blocked, parse error, damaged copy, no answer within 10 s), **the guide is not mounted at all**: no launcher,
no half-working panel. The site's own navigation is the fallback.

## What ships

| Piece | Where |
|---|---|
| Reviewed answers, destinations (relative paths on rithavo.com), page-aware starter questions | `app/ritanya_knowledge.py` |
| Validation, matcher, limiter, `POST /api/ritanya/chat` (server engine only), `GET /api/ritanya/status` (always) | `app/ritanya_chat.py` |
| Browser engine (generated) | `assets/ritanya/engine.js` (requested as `engine.js?v=<fingerprint>` so a new release is never answered by an old cached copy) |
| Launcher and chat panel | `assets/ritanya/ritanya.js`, `ritanya.css` |
| One script tag right after `<body>` on 19 pages (not sign-in, sign-up, reset-password, onboarding, admin) | `<script src="/assets/ritanya/ritanya.js" defer>` |
| Sign-out clearing of the guide's tab storage | `pwa.js` `clearPrivateState` (removes `rv_ritanya_*`) |
| Tests | `test_ritanya_*.py`, `test_public_wording.py`, `test_no_control_characters.py`; real Chrome: `tests/e2e/ritanya_*_check.js` |

## Activation prerequisites (Production is a separate, explicit step)
1. Founder approval to set `RITHAVO_RITANYA_MODE` in Production (`browser` recommended: no per-message server work, no limiter to defeat, no
   address ever handled), then redeploy. With `deterministic`, also `RITHAVO_RITANYA_IP_HEADER=x-forwarded-for` (without it everyone shares one
   limiter bucket). In `deterministic` mode a client can beat the per-instance limit by sending requests in parallel (measured on Vercel).
2. The Card answer assumes the Card is not available on rithavo.com: release after the Card-handoff removal (`feature/card-handoff-removal`).
3. Privacy wording reviewed and approved (draft and review notes on `launch/privacy-and-wording`; not merged).
4. Ritanya's pictures: twelve founder-approved files in `assets/ritanya/art/` (six faces, six poses; `PROVENANCE.md` records the 2026-11-06 approval and that rights are still unverified). The hero on the homepage, the signed-in home card and the chat panel use them. Which face and pose show is chosen in `ritanya.js` (`LOOKS`) from the reply's topic and outcome. The pictures are pinned by SHA-256 in `tests/test_ritanya_integration.py`.

## Rollback
Unset `RITHAVO_RITANYA_MODE` (or set `RITHAVO_RITANYA_PAUSED=true`) and redeploy. To go back from the browser engine to the server engine, set
`deterministic` and redeploy; nothing else changes.
