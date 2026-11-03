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
The status route (`/api/ritanya/status`) always exists and is edge-cached. Off or paused it answers `{"enabled": false}`. The page asks the
server again whenever its last answer is older than 2 minutes (when the panel opens, when the tab becomes visible again, before each
message). If the answer is "off" the whole guide is removed from the page and what the visitor typed is cleared; if the server cannot be
reached the guide keeps working. So after a redeploy with the guide paused or off, every open page drops it within about 2 minutes plus the
edge cache (60 s), and no page loads it afterwards.

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
4. The portrait stays off. `AVATAR_SRC` in `ritanya.js` is `null`.

## Rollback
Unset `RITHAVO_RITANYA_MODE` (or set `RITHAVO_RITANYA_PAUSED=true`) and redeploy. To go back from the browser engine to the server engine, set
`deterministic` and redeploy; nothing else changes.
