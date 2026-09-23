"""
Customer-Facing Profile Routing Correction.

rithavo.com is the one customer-facing Rithavo surface; app.rithavo.com
must never be exposed to a customer's browser. The actual Profile 2.0
implementation (structured experience_entries, Skills, Professional
DNA, completeness -- the whole Professional Background Unification
phase) stays entirely in the sibling rithavo-career-profile codebase,
unchanged and un-duplicated here. This module is a thin, transparent
HTTP reverse proxy for that one surface: every request under
/profile/edit is forwarded server-to-server to the sibling's real,
existing /profile and /profile/edit routes, authenticated via the same
shared INTERNAL_SERVICE_SECRET internal_client.py already uses for
Explore/Admin/Mentorship -- never a career-profile session cookie
(none exists for a rithavo.com-authenticated customer).

Path mapping (deliberately simple and mechanical, no per-feature
special-casing):
  rithavo.com  /profile/edit            -> app.rithavo.com /profile
  rithavo.com  /profile/edit/<rest>     -> app.rithavo.com /profile/<rest>

/profile (bare) is the sibling's rich, read-only-plus-actions VIEW --
the unified Professional Background timeline with inline Edit/Delete,
Skills chips, and Professional DNA -- landed on directly, matching what
"Edit your profile" should actually open onto. /profile/edit itself
(the sibling's scalar-field + experience forms page), /profile/
experience/*, /profile/skills/*, /profile/dna/*, and /profile/edit/
resume* all fall out of the same single <rest> mapping for free.

Every relative link/form-action the sibling's own templates emit
(href="/profile...", action="/profile...") and every redirect Location
header it returns are already bare, sibling-relative paths -- exactly
the one thing this proxy has to translate, by prepending "/profile/
edit" to each, so a click or form submit inside the proxied page keeps
the browser on rithavo.com and lands back on this same proxy instead of
a 404 or the sibling's own (unreachable, cookie-less) origin. Nothing
else about the returned markup is touched.

/static/... references (the sibling's stylesheet) are rewritten and
proxied the same way, so the returned page keeps its existing look.

Known, disclosed gap: photo upload (/photo/*) and the "Update Profile
from Resume" flow are not covered by this mapping (their links inside
the proxied page are left as sibling-relative /photo/... paths, which
are not proxied here) -- out of scope for this specific routing
correction; a customer using Professional Background/Skills/DNA is
unaffected.
"""

import re

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse, Response

import config

_REQUEST_TIMEOUT_SECONDS = 15.0
_PROXY_PREFIX = "/profile/edit"

# Rewrites a bare, sibling-relative reference (href="/profile...",
# action="/profile...", or the sibling's own /static/... stylesheet) to
# route back through this proxy instead. Deliberately narrow (only
# these two known-safe roots) so it can never touch an unrelated
# absolute URL or a fragment-only link.
_LINK_RE = re.compile(r'(href|action)="(/profile(?:/[^"]*)?|/static/[^"]*)"')


def _rewrite_target(path: str) -> str:
    if path.startswith("/profile"):
        return _PROXY_PREFIX + path[len("/profile"):]
    return _PROXY_PREFIX + path  # /static/... -> /profile/edit/static/...


def _rewrite_links(html: str) -> str:
    return _LINK_RE.sub(lambda m: f'{m.group(1)}="{_rewrite_target(m.group(2))}"', html)


def _rewrite_location(location: str) -> str:
    if location.startswith("/profile"):
        return _rewrite_target(location)
    # The one other redirect the sibling's /profile* routes ever issue:
    # "/onboarding/start" for a caller with no career_profiles row yet
    # (see app/routers/profile.py's own _require_user-guarded routes).
    # That path is the sibling's own onboarding wizard, which doesn't
    # exist on rithavo.com and must never be exposed -- rithavo.com's
    # own sign-in flow already sends a customer with no profile to
    # rithavo.com's own /onboarding/ before they could ever reach here
    # (see main.py's post-login redirect), so this is a defensive
    # fallback for that same "no profile yet" condition, not the normal
    # path.
    return "/onboarding/"


async def proxy_profile_edit(request: Request, rest: str = "") -> Response:
    session_user_id = request.session.get("user_id")
    if not session_user_id:
        next_path = request.url.path
        return RedirectResponse(f"/sign-in/?next={next_path}", status_code=302)

    if not config.INTERNAL_SERVICE_SECRET:
        raise HTTPException(status_code=503, detail="Profile service is not configured.")

    if rest.startswith("static/"):
        target_path = "/" + rest  # the sibling's own stylesheet, not a /profile sub-path
    else:
        target_path = "/profile" + (("/" + rest) if rest else "")
    target_url = f"{config.INTERNAL_SERVICE_BASE_URL}{target_path}"
    headers = {
        "Authorization": f"Bearer {config.INTERNAL_SERVICE_SECRET}",
        "X-Rithavo-User-Id": str(session_user_id),
    }
    content_type = request.headers.get("content-type")
    if content_type:
        headers["Content-Type"] = content_type
    body = await request.body()

    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS, follow_redirects=False) as client:
            resp = await client.request(
                request.method, target_url, params=request.query_params, content=body, headers=headers,
            )
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Profile service is temporarily unavailable.")

    if resp.status_code in (301, 302, 303, 307, 308) and "location" in resp.headers:
        return RedirectResponse(_rewrite_location(resp.headers["location"]), status_code=resp.status_code)

    resp_content_type = resp.headers.get("content-type", "")
    body_out = resp.content
    if "text/html" in resp_content_type:
        body_out = _rewrite_links(resp.content.decode("utf-8", errors="replace")).encode("utf-8")

    passthrough_headers = {}
    if resp_content_type:
        passthrough_headers["content-type"] = resp_content_type
    return Response(content=body_out, status_code=resp.status_code, headers=passthrough_headers)
