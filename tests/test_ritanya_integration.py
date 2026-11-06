"""
Ritanya production-integration gates (no browser; the browser journeys are in tests/e2e/ritanya_browser_check.js):

  * the status route exists ONLY when the assistant is enabled (flag off = no route = no launcher);
  * every page-aware starter question is answered by a reviewed topic, never "I don't know";
  * the launcher script talks only to /api/ritanya/*, sets no cookie, renders server text with textContent only,
    and never names another host (one-domain rule);
  * the script is on the right pages and not on sign-in / sign-up / reset / onboarding / admin;
  * prices stay off and availability is never asserted.
"""

import hashlib
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
ASSET_VERSION = "20261106"   # the cache-busting stamp every page carries on the scripts and stylesheet that changed in this release
TAG = f'<script src="/assets/ritanya/ritanya.js?v={ASSET_VERSION}" defer></script>'


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
    assert "Not yet available for purchase." in K.AVAILABILITY_NOTE and "launch" not in K.AVAILABILITY_NOTE.lower()


def test_card_answer_says_the_card_is_not_available_and_promises_nothing():
    reply = K.TOPICS["card"].reply
    assert "isn't available on rithavo.com" in reply
    # founder-approved positioning: the Card is the foundational identity built from the Profile and mandatory for every member
    assert "foundation" in reply and "built from your Rithavo Profile" in reply and "mandatory for every member" in reply
    # but it is never promised, dated or described as something a visitor can open, create or view today
    assert not re.search(r"coming|soon|will be|launch|generate|view your|share your|download", reply, re.I)
    assert "isn't available on rithavo.com" in K.TOPICS["profile"].more
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


ART = ROOT / "assets" / "ritanya" / "art"
# The twelve cut-outs and the one portrait the founder approved on 2026-11-06 (assets/ritanya/art/PROVENANCE.md), pinned by SHA-256. Anything else
# added to assets/ritanya, or any change to these files, fails this test: a new picture needs its own recorded approval.
APPROVED_ART = {
    "face-analytical.webp": "165da91d1964c76b097a0ba17cefe1ab925bb2a6b0870379825b5befdafaf696",
    "face-encouraging.webp": "f9b28720f341334b9085fdd7d0d2598e6334827f665c7c75b1e677df89e93672",
    "face-neutral.webp": "188910e2e5dabb7f19aedb107660d26daed181a70ba2702bf5c37aa6a804b43a",
    "face-observant.webp": "311a7725c22c69ae3248f5b04ed398de0fa58580641dc21f47f8eda4eecba8cc",
    "face-thoughtful.webp": "417ebbbb5716dbe477d0c525e6d6b2a6517e60a71efc927c4ad81d8886302888",
    "face-warm.webp": "4d8c4b650981951632620c2cc877db6479fcb487a05ff7a8c87c17836de1827f",
    "portrait-hero.webp": "fe2c0da8d23b51114487f192acfa41160a649e8734abf1220ee399b8943ec1cd",
    "pose-explain.webp": "8a53c44f7c2169c37fcfeafb78c12f6d6849aed67aaef349c879c9eebf538759",
    "pose-look.webp": "4ee35a49ae8bb3e2034c4ce072726e7bab3fccdf26b93217f114b5352d88f4b4",
    "pose-observe.webp": "ac6a185130b5369899777af11717d78ce67f748f5e6d70eb8acd32f5251efd9f",
    "pose-point.webp": "8f46f86650f8ddb2de51683f699b3d10ae2a9cec2afbaf91e0b5e0ea371bd9f0",
    "pose-present.webp": "5f202df1b5e674197507f1ffe91b46b1e3f991905b7f6be078a6bb2595a6f1ed",
    "pose-walk.webp": "6c5956323eee860725961b69246c842717695058f2e4f0744444299d6da4dddf",
}


def test_only_the_twelve_approved_pictures_are_present_and_unaltered():
    found = {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in ART.iterdir() if f.suffix.lower() in {".webp", ".png", ".jpg", ".jpeg", ".gif", ".avif"}}
    assert found == APPROVED_ART
    # no other image anywhere in assets/ritanya
    others = [f.name for f in (ROOT / "assets" / "ritanya").rglob("*") if f.is_file() and f.suffix.lower() in {".webp", ".png", ".jpg", ".jpeg", ".gif", ".avif"} and f.parent != ART]
    assert others == []
    assert (ART / "PROVENANCE.md").is_file()


def test_the_provenance_note_records_the_approval_and_leaves_rights_unverified():
    note = (ART / "PROVENANCE.md").read_text(encoding="utf-8")
    assert "2026-11-06" in note and "unverified" in note.lower()


def _looks():
    block = re.search(r"var LOOKS = \{(.*?)\n  \};", JS, re.S).group(1)
    return {m[0]: (m[1], m[2]) for m in re.findall(r'(\w+): \["(\w+)", "(\w+)"\]', block)}


def test_every_look_names_a_real_topic_and_real_pictures():
    looks = _looks()
    assert looks, "LOOKS not found"
    for topic, (face, pose) in looks.items():
        assert topic in K.TOPICS, topic
        assert (ART / f"face-{face}.webp").is_file() and (ART / f"pose-{pose}.webp").is_file(), (topic, face, pose)
    assert set(K.TOPICS) == set(looks)           # every topic has its own look; none silently uses the default
    faces = {f for f, _ in looks.values()} | {"neutral"}       # neutral is the launcher's resting face
    poses = {p for _, p in looks.values()}
    assert faces == {n[5:-5] for n in APPROVED_ART if n.startswith("face-")}
    assert poses == {n[5:-5] for n in APPROVED_ART if n.startswith("pose-")}


def test_the_script_never_builds_a_picture_path_from_typed_or_server_text():
    # the only place a file name is made is setLook / preload / avatar, from the fixed FACES and POSES lists
    assert 'var FACES = ["neutral", "warm", "thoughtful", "observant", "analytical", "encouraging"];' in JS
    assert 'var POSES = ["point", "explain", "present", "observe", "look", "walk"];' in JS
    assert "FACES.indexOf(pair[0]) >= 0 && POSES.indexOf(pair[1]) >= 0" in JS
    assert "cleanLook(" in JS and "AVATAR_SRC" not in JS


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


# ------------------------------------------------------------------ the browser engine mode

def test_browser_mode_has_no_chat_route_and_tells_the_page_to_use_the_engine(monkeypatch):
    registered, client = mounted_client(monkeypatch, "browser")
    assert registered is False                                    # chat is not registered: nothing can be sent to a server
    assert client.post("/ritanya/chat", json={"message": "hi"}).status_code == 404
    assert not hasattr(client.app.state, "ritanya_limiter")       # no limiter, no client address ever handled
    r = client.get("/ritanya/status")
    body = r.json()
    assert r.status_code == 200 and body["enabled"] is True and body["engine"] == "browser" and body["live_ai"] is False
    assert body["disclosure"] == K.DISCLOSURE_BROWSER and "not sent to Rithavo or anyone else" in body["disclosure"]
    assert body["recheck_seconds"] == 120 and body["greeting"] == K.GREETING
    assert re.fullmatch(r"[0-9a-f]{12}", body["engine_version"])
    assert body["engine_version"] == hashlib.sha256(rc.ENGINE_FILE.read_bytes()).hexdigest()[:12]
    assert "max-age=60" in r.headers["cache-control"] and "set-cookie" not in r.headers


def test_browser_mode_kill_switch_turns_the_guide_off_for_every_page(monkeypatch):
    registered, client = mounted_client(monkeypatch, "browser", paused="true")
    r = client.get("/ritanya/status")
    assert r.json() == {"enabled": False} and "s-maxage=300" in r.headers["cache-control"]
    assert client.post("/ritanya/chat", json={"message": "hi"}).status_code == 404


def test_server_mode_status_declares_the_server_engine_and_no_engine_version(monkeypatch):
    registered, client = mounted_client(monkeypatch, "deterministic")
    body = client.get("/ritanya/status").json()
    assert body["engine"] == "server" and "engine_version" not in body and body["disclosure"] == K.DISCLOSURE


def test_the_page_and_the_server_agree_on_how_long_an_answer_is_trusted():
    ms = int(re.search(r"var RECHECK_MS = (\d+);", JS).group(1))
    assert ms == rc.RECHECK_SECONDS * 1000


def test_the_client_removes_the_whole_guide_when_the_server_says_it_is_off():
    assert re.search(r"if \(cfg === null\) \{ teardown\(\); done\(false\); return; \}", JS)
    assert "store(KEY_CONVERSATION, null);" in JS[JS.index("function teardown()"):JS.index("function teardown()") + 400]
    assert 'visibilitychange' in JS and "recheck(function () {" in JS
