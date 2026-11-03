"""
Ritanya Phase A -- the deterministic, zero-provider assistant endpoint.

POST /ritanya/chat  (served as /api/ritanya/chat in production by api/index.py)

What this is: a fixed-information guide. It matches a short message against
reviewed rules and answers from app/ritanya_knowledge.py. There is NO model,
NO provider, NO network call and NO database access in this module.

What it is not: it does not assess anyone, score anything, recommend a career,
read an account, or generate text. Personal-assessment requests are refused
and pointed at the products that actually produce those results.

Production state: DISABLED. mount() registers nothing unless
RITHAVO_RITANYA_MODE=deterministic is set in the environment. Nothing in the
repository sets it.

Design decisions (each one tested in tests/test_ritanya_chat.py):

  * Stateless and cookie-free. The browser keeps the conversation and sends
    only `last_topic` (a topic id) and `unknown_streak` (a small integer)
    with each message. This avoids a new cookie (the Privacy page says the
    site uses none for tracking) and means the server stores nothing.
  * The existing `rv_sid` analytics cookie is NOT used as an identity or a
    rate-limit key: it is unsigned and any client can mint a fresh value, so
    it cannot limit anything. The existing app/rate_limit.py is not reused
    either: it never evicts keys, takes no lock, and is per process.
  * Rate limiting is per client address plus a per-process global cap, with
    bounded memory and a lock. On serverless hosting that cap is per
    instance, NOT global. A cap that holds across instances needs a shared
    store, which is a database change and is deliberately not made here.
  * Request validation is strict and manual: size cap, content type, exact
    key set, length, control characters, enumerated ids. Error bodies are
    fixed strings and never echo the input.
  * Logging records an opaque request id, an outcome, our own intent label
    and timing. It never records the message, the address, or any content.
  * Every link in a response comes from DESTINATIONS (relative paths on
    rithavo.com). A message cannot cause any other link to be produced.
"""

import json
import logging
import os
import re
import threading
import time
import unicodedata
import uuid
from collections import OrderedDict, deque

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app import ritanya_knowledge as K

logger = logging.getLogger("rithavo_web.ritanya")

MODE_ENV = "RITHAVO_RITANYA_MODE"          # "off" (default) or "deterministic"
PAUSE_ENV = "RITHAVO_RITANYA_PAUSED"       # "true" -> every request answers 503 (kill switch)
IP_HEADER_ENV = "RITHAVO_RITANYA_IP_HEADER"  # name of a TRUSTED proxy header carrying the client address

MAX_BODY_BYTES = 2048
MAX_MESSAGE_CHARS = 400
MAX_UNKNOWN_STREAK = 10
ALLOWED_KEYS = frozenset({"message", "last_topic", "unknown_streak"})

SCOPE = "approved-product-information"

router = APIRouter(prefix="/ritanya")


# ------------------------------------------------------------------ configuration

def _mode() -> str:
    return os.environ.get(MODE_ENV, "off").strip().lower()


def _paused() -> bool:
    return os.environ.get(PAUSE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.environ.get(name, "").strip())
    except ValueError:
        return default
    return max(lo, min(hi, value))


# ------------------------------------------------------------------ rate limiting

class ChatLimiter:
    """Sliding-window limiter: one window per client key plus one global window.

    Bounded: at most `max_keys` client windows are held; the least recently
    seen are dropped first, and expired windows are purged. Locked, so
    concurrent requests cannot corrupt the counters. Per process only."""

    def __init__(self, key_limit=20, key_window=300, global_limit=600, global_window=300,
                 max_keys=5000, clock=time.monotonic):
        self.key_limit, self.key_window = key_limit, key_window
        self.global_limit, self.global_window = global_limit, global_window
        self.max_keys, self.clock = max_keys, clock
        self._keys = OrderedDict()   # key -> deque of timestamps
        self._global = deque()
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls):
        return cls(
            key_limit=_int_env("RITHAVO_RITANYA_KEY_LIMIT", 20, 1, 1000),
            key_window=_int_env("RITHAVO_RITANYA_KEY_WINDOW_SECONDS", 300, 10, 86400),
            global_limit=_int_env("RITHAVO_RITANYA_GLOBAL_LIMIT", 600, 1, 1000000),
            global_window=_int_env("RITHAVO_RITANYA_GLOBAL_WINDOW_SECONDS", 300, 10, 86400),
        )

    @staticmethod
    def _trim(window, now, span):
        cutoff = now - span
        while window and window[0] <= cutoff:
            window.popleft()

    def allow(self, key: str):
        """Returns (allowed, retry_after_seconds)."""
        with self._lock:
            now = self.clock()
            self._trim(self._global, now, self.global_window)
            if len(self._global) >= self.global_limit:
                return False, max(1, int(self._global[0] + self.global_window - now) + 1)
            window = self._keys.get(key)
            if window is None:
                window = deque()
                self._keys[key] = window
            else:
                self._keys.move_to_end(key)
            self._trim(window, now, self.key_window)
            if len(window) >= self.key_limit:
                return False, max(1, int(window[0] + self.key_window - now) + 1)
            window.append(now)
            self._global.append(now)
            if len(self._keys) > self.max_keys:
                self._evict(now)
            return True, 0

    def _evict(self, now):
        # Least recently used first, O(1) per dropped key. (A full scan for expired windows on every request once the
        # table is full cost about 0.5 ms each under the lock.) Expired windows are cleared lazily when their key returns.
        while len(self._keys) > self.max_keys:
            self._keys.popitem(last=False)

    def size(self) -> int:
        with self._lock:
            return len(self._keys)


_limiter_lock = threading.Lock()


def _limiter(app) -> ChatLimiter:
    existing = getattr(app.state, "ritanya_limiter", None)
    if existing is None:
        with _limiter_lock:
            existing = getattr(app.state, "ritanya_limiter", None)
            if existing is None:
                existing = ChatLimiter.from_env()
                app.state.ritanya_limiter = existing
    return existing


def client_key(request: Request) -> str:
    """The address a request is limited by. Uses the header named in
    RITHAVO_RITANYA_IP_HEADER only if that is set (it must be a header the
    hosting proxy sets and a client cannot forge); otherwise the socket peer.
    Never logged."""
    header = os.environ.get(IP_HEADER_ENV, "").strip().lower()
    if header:
        value = request.headers.get(header, "")
        first = value.split(",")[0].strip()
        if first:
            return first[:64]
    host = request.client.host if request.client else ""
    return (host or "unknown")[:64]


# ------------------------------------------------------------------ validation

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏‪-‮⁠-⁯﻿]")


def clean_message(raw: str) -> str:
    text = unicodedata.normalize("NFC", raw)
    text = _CONTROL.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def validate_payload(raw: bytes):
    """Returns (data, None) or (None, error_code). Never raises, never echoes."""
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None, "invalid_request"
    if not isinstance(parsed, dict) or not set(parsed) <= ALLOWED_KEYS or "message" not in parsed:
        return None, "invalid_request"
    message = parsed["message"]
    if not isinstance(message, str):
        return None, "invalid_request"
    message = clean_message(message)
    if not 1 <= len(message) <= MAX_MESSAGE_CHARS:
        return None, "invalid_request"
    last_topic = parsed.get("last_topic")
    if last_topic is not None and (not isinstance(last_topic, str) or last_topic not in K.TOPICS):
        return None, "invalid_request"
    streak = parsed.get("unknown_streak", 0)
    if isinstance(streak, bool) or not isinstance(streak, int) or not 0 <= streak <= MAX_UNKNOWN_STREAK:
        return None, "invalid_request"
    return {"message": message, "last_topic": last_topic, "unknown_streak": streak}, None


# ------------------------------------------------------------------ the matcher

def _norm(message: str) -> str:
    # a bare "rithavo.com" is the company; any subdomain of it (app.rithavo.com, ...) is left intact so it is caught as a host
    text = re.sub(r"(?<![a-z0-9.\-])rithavo\.com", "rithavo", message.lower())
    text = re.sub(r"[^a-z0-9@₹\s.]", " ", text)
    text = re.sub(r"(?<![a-z0-9])\.|\.(?![a-z0-9])", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _rx(pattern: str):
    return re.compile(pattern, re.I)


_HOSTLIKE = _rx(r"\b[a-z0-9-]+\.(com|in|org|net|io|co|ai|app|dev|xyz|me|info|gov|edu|uk|us|ly)\b|https?|www\b")
_INJECTION = _rx(
    r"ignore (all |any |the |your )?(previous|prior|above|earlier|preceding)|disregard|forget (all |your )?(previous|prior|instructions|rules)"
    r"|system prompt|your (instructions|prompt|rules|guidelines|programming)|\b(reveal|show|print|repeat|leak|share|output)\b.{0,25}\b(prompt|instructions?|secrets?|passwords?|credentials?|configuration|environment variables?)\b"
    r"|jailbreak|developer mode|\bdan\b|pretend (to be|you are|you re)|you are now|from now on you|\bact as\b|\broleplay\b|\brole play\b"
    r"|\boverride\b|\bbypass\b|\bsudo\b|\bapi key\b|\bsecret key\b|\baccess token\b|open (the )?(link|url|site|website|page)|navigate to|redirect me|app rithavo|application rithavo"
)
_PERSONAL = [_rx(p) for p in (
    r"\b(analy[sz]e|review|evaluate|assess|rate|score|grade|critique|check|improve|fix|rewrite|proofread|optimi[sz]e)\b.{0,30}\b(my|this|our)\b.{0,15}\b(resume|cv|profile|application|cover letter|linkedin|experience|skills)\b",
    r"\bwhat (career|job|role|field|industry|path|profession)s?\b.{0,25}\b(should i|can i|do i|would i|suits? me|is (best|right) for me|are (best|right) for me|fits? me)\b",
    r"\b(which|what) (career|job|role|field|industry|path)\b.{0,30}\b(for me|suit|right|best|good|fits?)\b",
    r"\b(am i|would i be|could i be|do i have)\b.{0,20}\b(a good|good|well suited|qualified|ready|eligible|enough|the skills|chance|cut out)\b",
    r"\b(my|what is my|what s my|whats my|tell me my|give me my|show me my)\b.{0,15}\b(score|rating|fit|chances?|readiness|result|results|diagnosis|report|gaps?|strengths|weaknesses)\b",
    r"match my skills|careers? match|jobs? match|based on my (skills|experience|background|resume|profile)|for my (skills|background|experience)",
    r"\bwhat skills?\b.{0,20}\b(should|do|need|must|to)\b.{0,12}\b(i|build|learn|develop|improve|have|acquire)\b|skills? to (build|learn|develop)|build next|learn next",
    r"\b(recommend|suggest|advise|guide|tell)\b.{0,25}\b(me|my)\b.{0,25}\b(career|job|role|next|move|path|company|companies|opportunit)",
    r"\b(recommend|suggest|advise)\b.{0,30}\b(career|job|role|company|companies|path|field|industry|opportunit\w+|next step|next move)s?\b",
    r"\bshould i\b.{0,25}\b(switch|change|quit|leave|apply|take|accept|join|move|pivot|stay|resign|study|learn)\b",
    r"\bwhat should i do\b.{0,15}\b(with|about)\b.{0,10}\b(my|the) (career|life|job|future)",
    r"\b(salary|pay package|ctc|how much (will|would|can) i (earn|make|get paid))\b",
    r"\b(find|get|give|show|list|pick)\b.{0,15}\b(me|my)\b.{0,15}\b(jobs?|roles?|opportunit\w+|openings?|vacanc\w+|companies)",
)]

# Ordered: first match wins. Specific before general.
_TOPIC_RULES = [(topic, _rx(pattern)) for topic, pattern in (
    ("contact", r"refund|billing|invoice|order status|customer support|\bsupport\b|contact|email|reach (you|rithavo|out)|hello@|talk to (a |an )?(human|person|someone|team)|speak to"),
    ("pricing", r"\b(price|prices|pricing|cost|costs|how much|fee|fees|charge|charges|pay|paid|cheap|expensive|free|discount|rupees?|inr|afford)\b|₹"),
    ("launch", r"launch|availab|when (will|can|does|is)|live yet|open yet|coming soon|can i (buy|purchase|order|get)|\bbuy\b|purchase|\border\b"),
    ("stories", r"career stories|real stories|stories of|success stories|people who.{0,12}made (a|the) (move|switch|transition)|case stud"),
    ("mentorship", r"mentor|15 ?minute|session with|\bcoach"),
    ("card", r"\bcard\b"),
    ("profile", r"profile|add my (education|experience)|education and experience"),
    ("which_product", r"which (one|product|should|is (better|right|best))|difference between|compare|\bversus\b|\bvs\b|choose between|better for|where do i start|where should i start"),
    ("application_diagnosis", r"application diagnos|diagnostic|diagnosis|job description|tailored resume|resume (match|against|for a)|check a resume|against (a|one) (specific )?job|\bad\b"),
    ("career_intelligence", r"career intelligence|\bci\b|where (do )?i stand|where (my|your|a) career stands|positioning|\bposition\b|four (kinds|types|sizes)|transition|new field|change (careers?|fields?|industr|roles?)|switch (careers?|fields?)|career change|new role|new industry|major transition|career direction|direction|doing well"),
    ("explore", r"\bexplore\b|\bnews\b|\bfeed\b|articles?\b|stories\b"),
    ("join", r"\b(join|sign ?up|register|create (an )?account|sign ?in|log ?in|login|account)\b"),
    ("systems_growth", r"\bsystems?\b|\bgrowth\b|automation|small business|\bsmb\b"),
    ("privacy", r"privacy|my data|data (safe|secure|stored|collected|shared|protected)|are my messages|do you (store|save|log|keep|record|remember)|who (sees|can see|reads)|third part|sell my|tracking|cookies?|ai (service|provider|model)|train(ed|ing)? on"),
    ("method", r"how (does|do|did) (it|rithavo|this|that|they) (work|think|score|decide)|methodology|how (are|is) .{0,25}scored|\bats\b|evidence ?based"),
    ("ritanya", r"who are you|what are you|your name|are you (a |an |the )?(real|human|bot|ai|robot|chatbot|person|live)|what can you (do|help|answer)|how can you help|help me with|what do you know|are you (live|an ai)"),
    ("overview", r"what is rithavo|about rithavo|tell me about rithavo|what does rithavo|rithavo (is|does)|show me around|what s this|what is this|what do you offer|products|overview|how rithavo works|what rithavo"),
)]

_GREETING = _rx(r"^(hi|hello|hey|hiya|namaste|good (morning|afternoon|evening))( there| ritanya)?$")
_THANKS = _rx(r"^(thanks|thank you|thx|cheers|ok thanks|great thanks|got it)( ritanya)?$")
_VAGUE = _rx(r"^(help|help me|i need help|career|careers|job|jobs|resume|cv|advice|guidance|i m stuck|stuck|confused|lost|what should i do|can you help|can you help me|i have a question|question)$")
_MORE = _rx(r"^(more|tell me more|go on|and|and then|what else|why|how|how so|explain|explain more|can you explain|elaborate|details?)$")
_WHERE = _rx(r"^(where|where is it|where do i find it|where can i find it|link|the link|open it|take me there|show me|show me where|how do i get there|how do i open it)$")


def _action(key: str) -> dict:
    d = K.DESTINATIONS[key]
    return {"id": key, "label": d.label, "href": d.path, "canonical": K.CANONICAL_HOST + d.path, "needs_sign_in": d.needs_sign_in}


def _response(reply, *, topic=None, actions=(), suggestions=(), clarify=False, unknown=False, refused=False, intent="answer"):
    return {
        "reply": reply, "topic": topic,
        "actions": [_action(a) for a in actions],
        "suggestions": list(suggestions),
        "clarify": clarify, "unknown": unknown, "refused": refused,
        "scope": SCOPE, "live_ai": False,
        "_intent": intent,
    }


def _topic_response(topic_id: str, *, more=False):
    t = K.TOPICS[topic_id]
    return _response(t.more if more else t.reply, topic=topic_id, actions=t.actions, suggestions=t.suggestions,
                     intent=topic_id + ("_more" if more else ""))


def answer(message: str, last_topic=None, unknown_streak: int = 0) -> dict:
    """Pure function: message in, reviewed answer out. No I/O of any kind."""
    text = _norm(message)

    # 1. Anything that tries to steer the assistant, or to name another site, is out of bounds.
    if _INJECTION.search(text) or _HOSTLIKE.search(text):
        return _response(K.REFUSE_OUT_OF_BOUNDS, actions=("about",), suggestions=K.START_SUGGESTIONS, refused=True, intent="out_of_bounds")
    # 2. Requests that need an assessment of the person are refused, and pointed at the products.
    if any(rx.search(text) for rx in _PERSONAL):
        return _response(K.REFUSE_PERSONAL, topic="career_intelligence", actions=K.REFUSE_PERSONAL_ACTIONS,
                         suggestions=K.REFUSE_PERSONAL_SUGGESTIONS, refused=True, intent="personal_refused")
    # 3. Pleasantries.
    if _GREETING.match(text):
        return _response(K.GREETING, suggestions=K.START_SUGGESTIONS, intent="greeting")
    if _THANKS.match(text):
        return _response(K.THANKS, suggestions=K.START_SUGGESTIONS, intent="thanks")
    # 4. Topics.
    for topic_id, rx in _TOPIC_RULES:
        if rx.search(text):
            return _topic_response(topic_id)
    # 5. Short follow-ups lean on the last topic the browser reports.
    if _MORE.match(text):
        return _topic_response(last_topic, more=True) if last_topic else _response(K.NO_TOPIC_FOR_FOLLOWUP, suggestions=K.START_SUGGESTIONS, clarify=True, intent="followup_no_topic")
    if _WHERE.match(text):
        return _topic_response(last_topic) if last_topic else _response(K.NO_TOPIC_FOR_FOLLOWUP, suggestions=K.START_SUGGESTIONS, clarify=True, intent="followup_no_topic")
    # 6. Vague requests get a clarifying question, not a guess.
    if _VAGUE.match(text):
        return _response(K.CLARIFY, suggestions=K.CLARIFY_SUGGESTIONS, clarify=True, intent="clarify")
    # 7. Honest "I don't know".
    reply = K.UNKNOWN_REPEATED if unknown_streak >= 1 else K.UNKNOWN
    return _response(reply, actions=("contact",) if unknown_streak >= 1 else (), suggestions=K.UNKNOWN_SUGGESTIONS, unknown=True, intent="unknown")


# ------------------------------------------------------------------ the endpoint

_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
_ERROR_TEXT = {
    "invalid_request": "That request couldn't be read.",
    "unsupported_media_type": "Send JSON.",
    "too_large": "That request is too large.",
    "rate_limited": K.BUSY,
    "unavailable": K.UNAVAILABLE,
}


async def _read_capped(request: Request, cap: int):
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > cap:
        return None
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > cap:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/chat")
async def chat(request: Request):
    started, request_id = time.monotonic(), uuid.uuid4().hex[:12]

    def finish(status, outcome, intent="-", body=None, extra_headers=None):
        ms = int((time.monotonic() - started) * 1000)
        log = logger.info if status == 200 else logger.warning
        log("ritanya_chat request_id=%s outcome=%s intent=%s status=%d ms=%d", request_id, outcome, intent, status, ms)
        headers = dict(_HEADERS, **(extra_headers or {}))
        return JSONResponse(body, status_code=status, headers=headers)

    def error(status, code, extra_headers=None):
        return finish(status, code, body={"error": code, "message": _ERROR_TEXT[code], "request_id": request_id}, extra_headers=extra_headers)

    if _paused():
        return error(503, "unavailable")
    try:
        allowed, retry_after = _limiter(request.app).allow(client_key(request))
        if not allowed:
            return error(429, "rate_limited", {"Retry-After": str(retry_after)})
        if not request.headers.get("content-type", "").lower().startswith("application/json"):
            return error(415, "unsupported_media_type")
        raw = await _read_capped(request, MAX_BODY_BYTES)
        if raw is None:
            return error(413, "too_large")
        data, problem = validate_payload(raw)
        if problem:
            return error(400, problem)
        result = answer(data["message"], data["last_topic"], data["unknown_streak"])
        intent = result.pop("_intent")
        result["request_id"] = request_id
        return finish(200, "ok", intent, result)
    except Exception as exc:  # fail safe: a generic body, and only the exception TYPE is logged
        logger.warning("ritanya_chat request_id=%s outcome=error error_type=%s", request_id, type(exc).__name__)
        return JSONResponse({"error": "unavailable", "message": K.UNAVAILABLE, "request_id": request_id}, status_code=503, headers=_HEADERS)


@router.get("/status")
async def status(request: Request):
    """Tells the page whether to show the launcher at all, and gives it the reviewed text it displays before any
    message is sent. Registered only when the assistant is enabled, so with the flag off this route does not exist
    and the page shows no launcher. No input, no account data, identical for everyone."""
    if _paused():
        return JSONResponse({"enabled": False}, headers=_HEADERS)
    body = {
        "enabled": True, "live_ai": False, "scope": SCOPE,
        "greeting": K.GREETING, "disclosure": K.DISCLOSURE,
        "starters": {path: list(items) for path, items in K.PAGE_STARTERS.items()},
        "default_starters": list(K.START_SUGGESTIONS),
        "fallback_actions": [_action(a) for a in K.FALLBACK_ACTIONS],
        "messages": {"busy": K.BUSY, "unavailable": K.UNAVAILABLE},
    }
    # Short public cache so a page view does not cost a function call; short enough that the kill switch bites quickly.
    return JSONResponse(body, headers={"Cache-Control": "public, max-age=60, s-maxage=60", "X-Content-Type-Options": "nosniff"})


def mount(app) -> bool:
    """Registers the route only when explicitly enabled. Returns whether it did.
    Production default: RITHAVO_RITANYA_MODE is unset, so nothing is registered."""
    if _mode() != "deterministic":
        return False
    app.include_router(router)
    app.state.ritanya_limiter = ChatLimiter.from_env()
    return True
