# Privacy draft: review notes (not served, not legal advice)

Branch `launch/privacy-and-wording`. This draft is **unmerged and unpublished**. Nothing here claims legal adequacy; it only
records which statements in `privacy/index.html` were checked against the code and the live system, and which could not be.
Checked against: `main` at `bb2a5e3` plus `feature/ritanya-integration`, and the Production database (read-only aggregate counts).

## What changed in this revision (4 paragraphs; structure and headings untouched)

| Section | Change | Evidence |
|---|---|---|
| Your Rithavo account | Names Vercel (every request passes through its infrastructure; it may keep its own request records), Supabase (holds the account data on our behalf), and Zoho (receives the address and the text of the sign-in/password email) | `config.py` (database URL, SMTP settings come from environment), `app/auth.py` / `app/main.py` email send, Vercel runtime logs show the platform wrapper around every request |
| Cookies and analytics | Page-view event fires on public pages only; "no third-party analytics" kept, but "or tracking scripts" removed; Razorpay's checkout script is disclosed as loading when the Career Intelligence and Application Diagnosis pages open | `script.js` (beacon, 11 public pages); `application-diagnosis/index.html:13` and `career-intelligence/index.html:43` load `https://checkout.razorpay.com/v1/checkout.js` unconditionally in `<head>`; real-browser run recorded requests to `cdn.razorpay.com` and `api.razorpay.com` on both pages |
| Ritanya | Message used only in memory to pick a reply; passes through Vercel; conversation cleared on New conversation, **sign-out** or tab close; the on/off flag also kept in the tab; address held in server memory, not saved or logged, removed within about ten minutes of the last message once that server handles another request, or sooner on restart | `app/ritanya_chat.py` (no storage, log line has request id/outcome/topic/duration only; limiter purges expired addresses once per window); `pwa.js` `clearPrivateState` removes `rv_ritanya_*`; Vercel runtime logs searched for hostile text and spoofed addresses: none |
| Installed app | "Pages that need you to be signed in are always loaded from the network and are never stored" | `sw.js` `PRIVATE_PAGE_PREFIXES`; browser test: no signed-in page in any cache after visiting eight of them |

## Statements verified as true (kept)
- Sign-in cookie: signed, Secure (on whenever a database URL is configured, `config.py:124`), HttpOnly (Starlette default), 14 days (`max_age` default 1,209,600 s).
- Analytics cookie `rv_sid`: random identifier, one year, not HttpOnly; events store event type, path, time, coarse device type, the identifier and the user id when signed in. No IP address is stored. **The table has a `referrer` column but 0 of 227 production rows contain one.**
- Analytics event types in production: `page_view`, `login`, `ci_interest`, `ad_interest`.
- Cashfree receives the customer's email, phone number and an internal reference (`rithavo_user_<purchase id>`); no card or UPI details are received by Rithavo.
- Ritanya sets no cookie, has no model or provider, cannot read a session (tested: identical answers whatever cookie is sent).

## Disclosures I could NOT substantiate (decisions needed before anything is published)

1. **The intake-form flow is not found anywhere in the code or pages.** "What we collect", "Why we collect it", "Third parties", "How we handle payment", "Consent" and "How long we keep it" describe Tally, Google Sheets, Google Drive, Anthropic's Claude API, manual UPI transfer and an intake form. No page, script or backend module references any of them. If that process still runs outside this repository it needs confirming; if not, those sections are describing something that no longer happens. I left them **verbatim** (they are the existing Production wording) and did not delete or reword what I cannot verify.
2. **In-app data that the page does not mention:** accounts and Profile (education, experience), resumes uploaded for extraction (parsed locally, not stored by that endpoint), purchases and entitlements, Explore and Mentorship. The "What we collect" section covers only the intake form.
3. **Profile editing is proxied server-to-server to a sibling service** (`app/profile_proxy.py`, hosted at `app.rithavo.com`). Which providers that service uses, and what it stores, cannot be seen from this repository.
4. **Retention of analytics events:** no deletion or expiry exists in code. 227 events are stored, oldest 29 September 2026. A retention period cannot be stated until one is decided.
5. **Retention and contents of Vercel's own request records,** and where they are held. The draft says only that Vercel "may keep its own standard request records under its own policies".
6. **Supabase and Zoho:** data location/region, backups and retention are not visible from the code. The Supabase organisation holding the production database is on the Free plan; that does not change what the page must say but is relevant to any availability or retention promise.
7. **Razorpay:** why the script still loads (the server-side gateway choice happens after the page loads) and exactly what it collects. The draft only says that the browser contacts Razorpay and that Razorpay's policy governs it. Observed in a real browser: a "risk detection" bundle and a device identifier in the requests; I have not characterised that further. The statement "we do not use third-party tracking scripts" was removed because it could not be defended with this script present.
8. **How a deletion request is actually carried out.** The page promises deletion on request by email; there is no deletion tooling in the repository, so this is a manual process I cannot verify.
9. **"Last updated 31 October 2026"** is left as it was; set it when (and if) the page is published.
10. **Not addressed, not asserted:** the page names no legal entity, address or responsible person; it says nothing about children, international transfers, or the user's rights under any specific law. Whether any of that is required is a question for the founder's counsel.

## Product-copy suggestion (not legal wording)
The Ritanya panel's one-line disclosure does not tell visitors to avoid typing personal details. The text is not stored, but a short "please don't enter personal information" line is cheap and consistent with the policy.
