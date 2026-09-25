"""
Customer-facing Explore renderer (explore-feed.js): story fields come from
RSS feeds and the Admin editor, so anything placed inside an HTML template
must be rendered as text, never interpreted as markup. This executes the
file's own `esc`, `safeExternalUrl`, `cardMarkup` and `detailMarkup` under
Node and inspects the HTML they produce with a real HTML tokenizer.
"""

import json
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

SOURCE = (Path(__file__).resolve().parent.parent / "explore-feed.js").read_text(encoding="utf-8")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is required to execute explore-feed.js")

IMG_PAYLOAD = "<img src=x onerror=alert(1)>"
ATTR_PAYLOAD = '" onmouseover="alert(1)'
SCRIPT_PAYLOAD = "<script>alert(1)</script>"
LEGIT_URL = "https://www.bbc.co.uk/news/articles/c1j1896e973o?at_medium=RSS&at_campaign=rss"


def _segment(start_marker: str, end_marker: str) -> str:
    start = SOURCE.index(start_marker)
    return SOURCE[start:SOURCE.index(end_marker, start)]


def _run(story: dict, esc_override: str = None) -> dict:
    labels = _segment("  const STORY_TYPE_LABELS_FALLBACK", "  };\n") + "  };\n"
    functions = _segment("  function esc(value) {", "  function cardHtml(s) {")
    if esc_override:
        functions = functions.replace(_segment("  function esc(value) {", "  // The citation link"), esc_override + "\n")
    script = (
        labels + functions
        + 'const s = JSON.parse(require("fs").readFileSync(0, "utf8"));\n'
        + "process.stdout.write(JSON.stringify({card: cardMarkup(s), detail: detailMarkup(s), url: safeExternalUrl(s.source_url)}));\n"
    )
    out = subprocess.run(["node", "-e", script], input=json.dumps(story, ensure_ascii=False), capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


class _Parsed(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.attrs, self.chips, self.meta, self.classes = [], [], [], [], []
        self._collect = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.tags.append(tag)
        self.attrs.append((tag, a))
        cls = a.get("class", "")
        self.classes.append(cls)
        if tag == "span" and cls == "explore-chip":
            self._collect = self.chips; self.chips.append("")
        elif "explore-card-meta" in cls.split():
            self._collect = self.meta; self.meta.append("")

    def handle_endtag(self, tag):
        self._collect = None

    def handle_data(self, data):
        if self._collect is not None:
            self._collect[-1] += data


def _parse(markup: str) -> _Parsed:
    p = _Parsed()
    p.feed(markup)
    p.close()
    return p


def _anchors(p: _Parsed):
    return [a for tag, a in p.attrs if tag == "a"]


def _legit_story(**overrides) -> dict:
    story = {
        "id": 103, "headline": "Should promotion depend on how workers use AI?", "summary": "s",
        "story_type": "WORKPLACE", "story_type_label": "Workplace", "source_name": "BBC News",
        "published_at": "2026-09-25T10:00:00+00:00", "source_url": LEGIT_URL, "has_image": False,
        "industries": [{"id": 88, "name": "Professional & Business Services"}, {"id": 92, "name": "Human Resources"}],
        "why_it_matters": "",
    }
    story.update(overrides)
    return story


# ---- hostile values render as text -------------------------------------------------

@pytest.mark.parametrize("payload", [IMG_PAYLOAD, ATTR_PAYLOAD, SCRIPT_PAYLOAD])
def test_source_name_renders_as_text_on_card_and_detail(payload):
    out = _run(_legit_story(source_name=payload))
    card = _parse(out["card"])
    assert not {"img", "script"} & set(card.tags)
    assert card.meta == [payload + " · 2026-09-25"]
    assert not any(k.startswith("on") for _t, a in card.attrs for k in a)


@pytest.mark.parametrize("payload", [IMG_PAYLOAD, ATTR_PAYLOAD, SCRIPT_PAYLOAD])
def test_industry_names_render_as_text_in_the_detail_chips(payload):
    out = _run(_legit_story(industries=[{"id": 1, "name": payload}]))
    detail = _parse(out["detail"])
    assert not {"img", "script"} & set(detail.tags)
    assert not any(k.startswith("on") for _t, a in detail.attrs for k in a)
    assert payload in detail.chips


@pytest.mark.parametrize("payload", [IMG_PAYLOAD, ATTR_PAYLOAD, SCRIPT_PAYLOAD])
def test_type_labels_render_as_text(payload):
    out = _run(_legit_story(story_type_label=payload))
    for markup in (out["card"], out["detail"]):
        parsed = _parse(markup)
        assert not {"img", "script"} & set(parsed.tags)
        assert payload in parsed.chips


# ---- the source URL ------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "javascript:alert(1)", "JaVaScRiPt:alert(1)", "java\tscript:alert(1)", " javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>", "vbscript:msgbox(1)", "//evil.example/x", "/relative/path",
    ATTR_PAYLOAD, IMG_PAYLOAD, "not a url", "",
])
def test_non_http_or_unparseable_source_urls_render_no_link(bad):
    out = _run(_legit_story(source_url=bad))
    detail = _parse(out["detail"])
    assert _anchors(detail) == []
    assert "a" not in detail.tags
    assert not any(k.startswith("on") for _t, a in detail.attrs for k in a)
    assert out["url"] == ""


def test_an_http_url_carrying_an_attribute_breakout_stays_inside_the_href():
    tricky = 'https://x.example/a?b=1&c=2" onmouseover="alert(1)'
    detail = _parse(_run(_legit_story(source_url=tricky))["detail"])
    anchors = _anchors(detail)
    assert len(anchors) == 1
    assert anchors[0]["href"] == tricky            # the value is intact, as text...
    assert not any(k.startswith("on") for k in anchors[0])   # ...and cannot add attributes


def test_a_legitimate_source_url_is_unchanged():
    detail = _parse(_run(_legit_story())["detail"])
    (anchor,) = _anchors(detail)
    assert anchor["href"] == LEGIT_URL              # & and query string preserved, tracking params untouched
    assert anchor["target"] == "_blank" and anchor["rel"] == "noopener noreferrer"


def test_the_helper_returns_the_stored_string_unmodified():
    for url in (LEGIT_URL, "http://example.com/a b?x=1", "https://example.com:8443/p#frag"):
        assert _run(_legit_story(source_url=url))["url"] == url


# ---- normal values: the UI is unchanged ---------------------------------------------

def test_a_normal_story_renders_exactly_as_before():
    out = _run(_legit_story())
    card, detail = _parse(out["card"]), _parse(out["detail"])
    assert card.chips == ["Workplace"]
    assert card.meta == ["BBC News · 2026-09-25"]
    assert "explore-card-image-fallback" in card.classes and "img" not in card.tags
    assert detail.chips == ["Workplace", "Professional & Business Services", "Human Resources"]
    assert "h2" in detail.tags and "explore-story-body" in detail.classes
    assert "explore-why-it-matters" not in detail.classes


def test_image_and_why_it_matters_still_render_when_present():
    out = _run(_legit_story(has_image=True, why_it_matters="because"))
    card, detail = _parse(out["card"]), _parse(out["detail"])
    (card_img,) = [a for t, a in card.attrs if t == "img"]
    assert card_img["src"] == "/api/explore/103/image"   # same-origin: served by rithavo.com
    (detail_img,) = [a for t, a in detail.attrs if t == "img"]
    assert detail_img["src"] == "/api/explore/103/image"
    assert "explore-why-it-matters" in detail.classes


def test_missing_source_name_falls_back_to_rithavo_and_type_label_falls_back_to_the_slug_map():
    out = _run(_legit_story(source_name="", story_type_label=None, story_type="MA", published_at=None))
    card = _parse(out["card"])
    assert card.meta == ["Rithavo"]
    assert card.chips == ["Mergers & Acquisitions"]


def test_headline_and_summary_are_still_set_via_textContent_only():
    assert ".textContent = s.headline" in SOURCE and ".textContent = s.summary" in SOURCE
    assert "${s.headline" not in SOURCE and "${s.summary" not in SOURCE


# ---- control ---------------------------------------------------------------------------

def test_the_test_can_detect_the_bug_it_guards_against():
    """With escaping replaced by a pass-through, the same payloads DO
    inject markup -- so the assertions above are meaningful."""
    passthrough = "  function esc(value) { return String(value == null ? '' : value); }"
    out = _run(_legit_story(source_name=IMG_PAYLOAD, industries=[{"id": 1, "name": SCRIPT_PAYLOAD}]), esc_override=passthrough)
    assert "img" in _parse(out["card"]).tags
    assert "script" in _parse(out["detail"]).tags


def test_no_externally_sourced_field_is_interpolated_raw():
    for needle in ("${i.name}", "${s.story_type_label", "${s.source_url}", "${meta}", "${s.id}"):
        assert needle not in SOURCE, needle


def test_rendered_markup_never_points_the_browser_at_the_sibling_service():
    out = _run(_legit_story(has_image=True, why_it_matters="because"))
    assert "app.rithavo.com" not in out["card"]
    assert "app.rithavo.com" not in out["detail"]
    assert "app.rithavo.com" not in _run(_legit_story(has_image=False))["card"]
