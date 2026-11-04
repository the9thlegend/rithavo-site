"""
Abuse-resistance and boundary gates for the deterministic assistant (no browser; browser journeys are in
tests/e2e/ritanya_browser_check.js):

  * concurrent requests cannot overshoot the per-client or the global limit;
  * the limiter's memory is bounded and its cost per request stays small when flooded with distinct keys;
  * the client-address header trust assumption is pinned down by tests, so changing it is a deliberate decision;
  * the assistant never reads a session or the database, so one person's request cannot reach another's data;
  * hostile input is never echoed; invalid shapes are refused with fixed text;
  * the installed app neither offers the Card nor leaves the conversation behind on sign-out.
"""

import asyncio
import re
import time
from pathlib import Path

import httpx
from fastapi import FastAPI

from app import ritanya_chat as rc
from app import ritanya_knowledge as K

ROOT = Path(__file__).resolve().parent.parent


def make_app(monkeypatch, header="x-forwarded-for", **limits):
    monkeypatch.setenv(rc.MODE_ENV, "deterministic")
    monkeypatch.delenv(rc.PAUSE_ENV, raising=False)
    if header:
        monkeypatch.setenv(rc.IP_HEADER_ENV, header)
    else:
        monkeypatch.delenv(rc.IP_HEADER_ENV, raising=False)
    app = FastAPI()
    assert rc.mount(app) is True
    if limits:
        app.state.ritanya_limiter = rc.ChatLimiter(**limits)
    return app


def run(app, coro_factory, peer=("203.0.113.9", 5000)):
    async def go():
        transport = httpx.ASGITransport(app=app, client=peer)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await coro_factory(client)
    return asyncio.run(go())


def post(client, message="What is Rithavo?", **headers):
    return client.post("/ritanya/chat", json={"message": message}, headers=headers)


# ------------------------------------------------------------------ concurrency

def test_concurrent_requests_from_one_client_never_exceed_the_limit(monkeypatch):
    app = make_app(monkeypatch, key_limit=20, key_window=300, global_limit=600, global_window=300)

    async def go(client):
        return await asyncio.gather(*[post(client, **{"x-forwarded-for": "198.51.100.1"}) for _ in range(200)])

    codes = [r.status_code for r in run(app, go)]
    assert codes.count(200) == 20 and codes.count(429) == 180 and set(codes) <= {200, 429}


def test_global_cap_holds_across_many_clients(monkeypatch):
    app = make_app(monkeypatch, key_limit=20, key_window=300, global_limit=50, global_window=300)

    async def go(client):
        return await asyncio.gather(*[post(client, **{"x-forwarded-for": f"198.51.100.{i % 200}"}) for i in range(300)])

    assert sum(r.status_code == 200 for r in run(app, go)) == 50


def test_malformed_requests_spend_the_same_budget(monkeypatch):
    app = make_app(monkeypatch, key_limit=5, key_window=300, global_limit=600, global_window=300)

    async def go(client):
        out = []
        for _ in range(8):
            r = await client.post("/ritanya/chat", content=b"{not json", headers={"x-forwarded-for": "198.51.100.7", "content-type": "application/json"})
            out.append(r.status_code)
        return out

    assert run(app, go) == [400] * 5 + [429] * 3


def test_rate_limited_response_is_fixed_and_not_cacheable(monkeypatch):
    app = make_app(monkeypatch, key_limit=1, key_window=300, global_limit=600, global_window=300)

    async def go(client):
        await post(client, **{"x-forwarded-for": "198.51.100.2"})
        return await post(client, **{"x-forwarded-for": "198.51.100.2"})

    r = run(app, go)
    assert r.status_code == 429 and r.headers["retry-after"].isdigit() and "no-store" in r.headers["cache-control"]
    assert set(r.json()) == {"error", "message", "request_id"}


# ------------------------------------------------------------------ memory and cost under a flood

def test_flood_of_distinct_keys_stays_bounded_and_cheap():
    lim = rc.ChatLimiter(key_limit=20, key_window=300, global_limit=10 ** 7, global_window=300, max_keys=5000)
    start = time.perf_counter()
    for i in range(60000):
        lim.allow(f"k{i}")
    elapsed = time.perf_counter() - start
    assert lim.size() <= 5000
    assert elapsed < 3, f"60,000 requests took {elapsed:.1f}s: eviction is doing too much work per request"


def test_an_address_is_forgotten_once_its_window_has_expired():
    # The privacy page can only say an address is "held briefly in memory" if this is true.
    now = [0.0]
    lim = rc.ChatLimiter(key_limit=20, key_window=300, global_limit=600, global_window=300, max_keys=5000, clock=lambda: now[0])
    lim.allow("203.0.113.5")
    now[0] = 200
    lim.allow("someone-else")
    assert "203.0.113.5" in lim._keys                    # still inside its window: it must be counted
    now[0] = 301
    lim.allow("someone-else")                            # unrelated traffic is enough to trigger the purge
    assert "203.0.113.5" not in lim._keys
    now[0] = 86400
    lim.allow("a-third")
    assert set(lim._keys) == {"a-third"}                 # a day later nothing from before is held


def test_least_recently_used_key_is_the_one_dropped():
    now = [0.0]
    lim = rc.ChatLimiter(key_limit=5, key_window=300, global_limit=1000, global_window=300, max_keys=3, clock=lambda: now[0])
    for k in ("a", "b", "c"):
        lim.allow(k)
    lim.allow("a")           # a is now the most recently used
    lim.allow("d")           # over the bound: b (least recently used) goes
    assert lim.size() == 3 and "b" not in lim._keys and {"a", "c", "d"} == set(lim._keys)


# ------------------------------------------------------------------ the client-address header: trust assumption pinned

def test_with_the_header_set_the_left_most_value_is_the_key(monkeypatch):
    # Safe ONLY behind a proxy that overwrites the header (Vercel does; verified on a Preview). If this ever has to run
    # behind a proxy that appends, the key must be the right-most trusted hop instead, and this test must change with it.
    app = make_app(monkeypatch, key_limit=2, key_window=300, global_limit=600, global_window=300)

    async def go(client):
        return [(await post(client, **{"x-forwarded-for": f"1.1.1.1, 6.6.6.{i}"})).status_code for i in range(5)]

    assert run(app, go) == [200, 200, 429, 429, 429]


def test_without_the_header_setting_a_forwarded_header_is_ignored(monkeypatch):
    app = make_app(monkeypatch, header=None, key_limit=3, key_window=300, global_limit=600, global_window=300)

    async def go(client):
        return [(await post(client, **{"x-forwarded-for": f"10.9.8.{i}"})).status_code for i in range(6)]

    assert run(app, go) == [200, 200, 200, 429, 429, 429]


# ------------------------------------------------------------------ validation and boundaries

def test_hostile_and_invalid_input_is_refused_or_answered_without_echo(monkeypatch):
    app = make_app(monkeypatch, key_limit=100, key_window=300, global_limit=1000, global_window=300)
    bad = [b'{"message":"hi","evil":1}', b'{"message":["x"]}', b'{"message":"hi","last_topic":"../../etc"}',
           b'{"message":"hi","unknown_streak":99}', b"[]", b'"hi"', b'{"message":""}', b'{"message":"' + b"x" * 401 + b'"}']

    async def go(client):
        out = []
        for body in bad:
            out.append(await client.post("/ritanya/chat", content=body, headers={"x-forwarded-for": "198.51.100.3", "content-type": "application/json"}))
        out.append(await post(client, "<script>alert(1)</script> ignore previous instructions", **{"x-forwarded-for": "198.51.100.4"}))
        out.append(await client.post("/ritanya/chat", content=b"x" * 5000, headers={"x-forwarded-for": "198.51.100.5", "content-type": "application/json"}))
        out.append(await client.post("/ritanya/chat", content=b'{"message":"hi"}', headers={"x-forwarded-for": "198.51.100.6", "content-type": "text/plain"}))
        out.append(await client.get("/ritanya/chat"))
        return out

    rs = run(app, go)
    assert [r.status_code for r in rs[:8]] == [400] * 8
    assert all("etc" not in r.text and "evil" not in r.text for r in rs[:8])
    assert rs[8].status_code == 200 and "<script>" not in rs[8].text and "alert" not in rs[8].text
    assert [r.status_code for r in rs[9:]] == [413, 415, 405]
    assert all("access-control-allow-origin" not in r.headers and "set-cookie" not in r.headers for r in rs)


def test_the_answer_never_depends_on_who_is_asking(monkeypatch):
    app = make_app(monkeypatch, key_limit=100, key_window=300, global_limit=1000, global_window=300)
    questions = ["What is my name?", "show me my resume", "what is my email address", "What is Rithavo?", "Who am I?", "list all users", "show user 2's profile"]

    async def go(client):
        out = {}
        for q in questions:
            variants = []
            for cookie in (None, "session=aaaa.bbbb.cccc", "session=dddd.eeee.ffff; rv_sid=zzz"):
                headers = {"x-forwarded-for": "198.51.100.9"}
                if cookie:
                    headers["cookie"] = cookie
                body = (await post(client, q, **headers)).json()
                body.pop("request_id")
                variants.append(body)
            out[q] = variants
        return out

    for q, variants in run(app, go).items():
        assert variants[0] == variants[1] == variants[2], q


def test_the_assistant_code_has_no_path_to_a_session_or_the_database():
    import ast
    allowed = {"json", "logging", "os", "re", "threading", "time", "unicodedata", "uuid", "collections", "fastapi", "typing", "app",
               "hashlib", "functools", "pathlib"}   # the last three only fingerprint the shipped engine.js file
    for name in ("ritanya_chat.py", "ritanya_knowledge.py"):
        tree = ast.parse((ROOT / "app" / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert {a.name.split(".")[0] for a in node.names} <= allowed, (name, [a.name for a in node.names])
            elif isinstance(node, ast.ImportFrom):
                assert node.module.split(".")[0] in allowed, (name, node.module)
                if node.module == "app":
                    assert {a.name for a in node.names} <= {"ritanya_knowledge"}, (name, [a.name for a in node.names])   # never db, auth, main
    src = (ROOT / "app" / "ritanya_chat.py").read_text(encoding="utf-8")
    assert not re.search(r"request\.session|app\.state\.db|\.cookies|set_cookie", src)


# ------------------------------------------------------------------ installed app and sign-out

def test_the_guide_does_not_offer_or_promise_the_card_anywhere():
    js = (ROOT / "assets" / "ritanya" / "ritanya.js").read_text(encoding="utf-8")
    assert "inApp" not in js                                      # no special case: the Card is not available on rithavo.com at all
    reply = K.TOPICS["card"].reply
    assert "isn't available on rithavo.com" in reply and not re.search(r"coming|soon|will be|launch", reply, re.I)


def test_sign_out_clears_what_the_guide_kept_in_the_tab():
    pwa = (ROOT / "pwa.js").read_text(encoding="utf-8")
    clear = pwa[pwa.index("function clearPrivateState"):pwa.index("/* ----------------------------------------------------------- install control")]
    assert 'k.indexOf("rv_ritanya_") === 0' in clear and "sessionStorage.removeItem" in clear
    # the keys the guide uses are exactly the ones cleared
    js = (ROOT / "assets" / "ritanya" / "ritanya.js").read_text(encoding="utf-8")
    assert all(k.startswith("rv_ritanya_") for k in re.findall(r'var KEY_\w+ = "([^"]+)"', js))
