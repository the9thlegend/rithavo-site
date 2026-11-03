"""
Ritanya production-integration gates (no browser; the browser journeys are in tests/e2e/ritanya_browser_check.js):

  * the status route exists ONLY when the assistant is enabled (flag off = no route = no launcher);
  * every page-aware starter question is answered by a reviewed topic, never "I don't know";
  * the launcher script talks only to /api/ritanya/*, sets no cookie, renders server text with textContent only,
    and never names another host (one-domain rule);
  * the script is on the right pages and not on sign-in / sign-up / reset / onboarding / admin;
  * prices stay off and availability is never asserted.
"""

import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import ritanya_chat as rc
from app import ritanya_knowledge as K

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "assets" / "ritanya" / "ritanya.js").read_text(encoding="utf-8")
CSS = (ROOT / "assets" / "ritanya" / "ritanya.css").read_text(encoding="utf-8")
EXCLUDED_PAGES = {"sign-in", "sign-up", "reset-password", "onboarding", "admin", "offline"}   # offline: the PWA offline page (a different branch)
TAG = '<script src="/assets/ritanya/ritanya.js" defer></script>'


def mounted_client(monkeypatch, mode="deterministic", paused=None):
    if mode is None:
        monkeypatch.delenv(rc.MODE_ENV, raising=False)
    else:
        monkeypatch.setenv(rc.MODE_ENV, mode)
    if paused is None:
        monkeypatch.delenv(rc.PAUSE_ENV, raising=False)
    else:
        monkeypatch.setenv(rc.PAUSE_ENV, paused)
    app = FastAPI()
    registered = rc.mount(app)
    return registered, TestClient(app)


# ------------------------------------------------------------------ the flag

def test_flag_off_has_no_chat_route_and_status_says_disabled_from_the_edge_cache(monkeypatch):
    for mode in (None, "off", "", "true", "DETERMINISTIC-ish"):
        registered, client = mounted_client(monkeypatch, mode)
        assert registered is False
        # no chat route at all: nothing can be asked, nothing is processed
        assert client.post("/ritanya/chat", json={"message": "hi"}).status_code == 404
        # the page's one question per tab gets a cacheable "disabled" (no function run per new tab), never a launcher
        r = client.get("/ritanya/status")
        assert r.status_code == 200 and r.json() == {"enabled": False}
        assert "s-maxage=300" in r.headers["cache-control"] and "public" in r.headers["cache-control"] and "no-store" not in r.headers["cache-control"]
        assert r.headers["x-content-type-options"] == "nosniff" and "set-cookie" not in r.headers


def test_flag_on_serves_status_with_reviewed_text_only(monkeypatch):
    registered, client = mounted_client(monkeypatch)
    assert registered is True
    r = client.get("/ritanya/status")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True and body["live_ai"] is False
    assert body["greeting"] == K.GREETING and body["disclosure"] == K.DISCLOSURE
    assert "not a live AI" in body["disclosure"] and "can't see your account" in body["disclosure"]
    assert "max-age=60" in r.headers["cache-control"] and "private" not in r.headers["cache-control"]
    assert r.headers["x-content-type-options"] == "nosniff"
    blob = r.text
    assert "http://" not in blob and "app.rithavo" not in blob
    assert all(m.startswith("https://rithavo.com/") for m in re.findall(r"https?://[^\"\s]+", blob))   # the only host ever named
    for action in body["fallback_actions"]:
        assert action["href"].startswith("/") and not action["href"].startswith("//")
        assert action["canonical"] == "https://rithavo.com" + action["href"]


def test_kill_switch_hides_the_launcher_and_stops_chat(monkeypatch):
    registered, client = mounted_client(monkeypatch, paused="true")
    r = client.get("/ritanya/status")
    assert r.status_code == 200 and r.json() == {"enabled": False}
    assert "s-maxage" in r.headers["cache-control"]          # cacheable: the state changes only with a new deployment
    assert client.post("/ritanya/chat", json={"message": "hi"}).status_code == 503


def test_enabled_status_keeps_its_short_cache(monkeypatch):
    registered, client = mounted_client(monkeypatch)
    cc = client.get("/ritanya/status").headers["cache-control"]
    assert "s-maxage=60" in cc and "max-age=60" in cc


# ------------------------------------------------------------------ starters

def test_every_starter_is_answered_by_a_reviewed_topic():
    phrases = set(K.START_SUGGESTIONS)
    for items in K.PAGE_STARTERS.values():
        phrases.update(items)
    bad = []
    for phrase in sorted(phrases):
        out = rc.answer(phrase)
        if out["unknown"] or out["refused"] or out["clarify"] or not out["topic"]:
            bad.append((phrase, out["_intent"]))
    assert bad == []


def test_starter_pages_exist_on_rithavo_and_phrases_are_short():
    for path, items in K.PAGE_STARTERS.items():
        assert path.startswith("/") and path.endswith("/")
        page = ROOT / (path.strip("/") or ".") / "index.html"
        assert page.is_file(), path
        assert 1 <= len(items) <= 4
        assert all(len(p) <= 60 for p in items)


def test_every_answer_link_is_a_relative_path_on_rithavo():
    for topic_id in K.TOPICS:
        for action in rc._topic_response(topic_id)["actions"]:
            assert action["href"].startswith("/") and not action["href"].startswith("//")
            assert action["canonical"].startswith("https://rithavo.com/")


def test_prices_stay_disabled_and_availability_is_never_asserted():
    assert K.PRICES_ENABLED is False
    for question in ("How much does it cost?", "What is the price of Career Intelligence?", "Is it free?"):
        reply = rc.answer(question)["reply"]
        assert "₹" not in reply and "399" not in reply and "799" not in reply
    for topic in ("launch", "career_intelligence", "application_diagnosis"):
        text = K.TOPICS[topic].reply + K.TOPICS[topic].more
        assert "available now" not in text.lower() and "live now" not in text.lower()
    assert "available for purchase at launch" in K.AVAILABILITY_NOTE


def test_card_answer_does_not_promise_the_card_inside_the_installed_app():
    reply = K.TOPICS["card"].reply
    assert "web browser" in reply and "installed app" in reply
    assert not any(d.path.startswith("/card") for d in K.DESTINATIONS.values())


# ------------------------------------------------------------------ the launcher script

def test_launcher_talks_only_to_the_same_origin_ritanya_api():
    assert '"/api/ritanya/status"' in JS and '"/api/ritanya/chat"' in JS
    assert not re.search(r"https?://", (JS + CSS).replace("http://www.w3.org/2000/svg", ""))   # the SVG XML namespace is an identifier, not a request
    assert not re.search(r"[\"'`]//[a-z]", JS)   # no protocol-relative host
    assert 'credentials: "omit"' in JS


def test_launcher_sets_no_cookie_and_keeps_the_conversation_in_the_tab_only():
    assert "document.cookie" not in JS and "localStorage" not in JS and "indexedDB" not in JS
    assert "sessionStorage" in JS


def test_server_text_is_rendered_with_textContent_never_as_html():
    assert "innerHTML" not in JS and "insertAdjacentHTML" not in JS and "outerHTML" not in JS and "document.write" not in JS
    assert "eval(" not in JS and "new Function" not in JS
    assert "textContent" in JS


def test_links_are_validated_as_same_origin_relative_paths_before_use():
    assert re.search(r"function\s+safePath", JS)
    assert "^\\/(?![\\/\\\\])" in JS


def test_launcher_respects_the_bottom_navigation_and_safe_areas():
    assert "--rv-bottomnav-h" in CSS and "env(safe-area-inset-bottom" in CSS
    assert "@media (max-width: 640px)" in CSS and "prefers-reduced-motion" in CSS


def test_portrait_stays_off_until_rights_are_cleared():
    assert re.search(r"var\s+AVATAR_SRC\s*=\s*null\s*;", JS)
    assert not list((ROOT / "assets" / "ritanya").glob("*.webp")) and not list((ROOT / "assets" / "ritanya").glob("*.jpg"))
    for f in (ROOT / "assets" / "ritanya").rglob("*"):
        assert "portrait" not in f.name.lower() and "hero" not in f.name.lower()


# ------------------------------------------------------------------ placement

def test_script_is_on_the_right_pages_only():
    pages = [p for p in ROOT.rglob("index.html") if p.relative_to(ROOT).parts[0] not in
             {"design-lab", "ritanya-handoff", "tests", "app", "node_modules", ".git"}]
    assert len(pages) >= 20
    wrong = []
    for page in pages:
        rel = page.relative_to(ROOT)
        top = rel.parts[0] if len(rel.parts) > 1 else ""
        has = TAG in page.read_text(encoding="utf-8")
        should = top not in EXCLUDED_PAGES
        if has != should:
            wrong.append((rel.as_posix(), "has" if has else "missing"))
    assert wrong == []


def test_script_tag_sits_right_after_body_open_and_never_before_head_content():
    for page in ROOT.rglob("index.html"):
        text = page.read_text(encoding="utf-8")
        if TAG in text:
            assert re.search(r"<body>\s*" + re.escape(TAG), text), page
