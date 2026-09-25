"""
Admin page (admin/index.html): every value that originates outside the
page -- RSS-ingested story text, industry names, user emails -- must be
rendered as text, never as markup. The Admin story editor builds its form
from an HTML template string in browser JavaScript, so this executes the
page's own `esc` and `storyFormHtml` functions under Node and inspects
the HTML they produce with a real HTML tokenizer.
"""

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

ADMIN_HTML = (Path(__file__).resolve().parent.parent / "admin" / "index.html").read_text(encoding="utf-8")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is required to execute the Admin page's JS")

PAYLOAD_HEADLINE = '"><script>alert(1)</script><img src=x onerror=alert(1)>'
PAYLOAD_SUMMARY = "</textarea><script>alert(2)</script><b>bold</b>"
PAYLOAD_SOURCE_NAME = "' onfocus='alert(3)' x='"
PAYLOAD_SOURCE_URL = 'https://x.example/a?b=1&c=2" onmouseover="alert(4)'
PAYLOAD_BODY = "</textarea><iframe src=javascript:alert(5)></iframe>"
PAYLOAD_WHY = "<svg onload=alert(6)>"
PAYLOAD_INDUSTRY = "<b>Bold</b> & \"Co\""


def _extract(start_marker: str, end_marker: str) -> str:
    start = ADMIN_HTML.index(start_marker)
    return ADMIN_HTML[start:ADMIN_HTML.index(end_marker, start)]


def _render_story_form(story: dict, escape_source: str = None) -> str:
    esc_src = escape_source or _extract("  function esc(value) {", "\n\n  const STORY_TYPES")
    form_src = _extract("  function storyFormHtml(story) {", "\n\n  function openStoryForm")
    script = (
        'const STORY_TYPES = {GLOBAL: "Global", CAREER: "Career"};\n'
        f"let industryTreeCache = [{{parent: {{id: 1, name: {json.dumps(PAYLOAD_INDUSTRY)}}}, "
        f"children: [{{id: 2, name: {json.dumps(PAYLOAD_INDUSTRY)}}}]}}];\n"
        + esc_src + "\n" + form_src + "\n"
        + 'const input = JSON.parse(require("fs").readFileSync(0, "utf8"));\n'
        + "process.stdout.write(JSON.stringify(storyFormHtml(input)));\n"
    )
    out = subprocess.run(["node", "-e", script], input=json.dumps(story), capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


class _Collect(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.attrs = []          # (tag, {attr: value})
        self.textarea = {}       # name -> text
        self.option_text = []
        self._current = None

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs.append((tag, dict(attrs)))
        if tag == "textarea":
            self._current = ("textarea", dict(attrs).get("name"))
            self.textarea[self._current[1]] = ""
        elif tag == "option":
            self._current = ("option", None)
            self.option_text.append("")

    def handle_endtag(self, tag):
        if self._current and self._current[0] == tag:
            self._current = None

    def handle_data(self, data):
        if self._current and self._current[0] == "textarea":
            self.textarea[self._current[1]] += data
        elif self._current and self._current[0] == "option":
            self.option_text[-1] += data


def _parse(html_text: str) -> _Collect:
    collector = _Collect()
    collector.feed(html_text)
    collector.close()
    return collector


def _hostile_story() -> dict:
    return {
        "id": 7, "headline": PAYLOAD_HEADLINE, "summary": PAYLOAD_SUMMARY, "story_type": "GLOBAL",
        "source_name": PAYLOAD_SOURCE_NAME, "source_url": PAYLOAD_SOURCE_URL,
        "body": PAYLOAD_BODY, "why_it_matters": PAYLOAD_WHY,
        "industries": [{"id": 2, "name": PAYLOAD_INDUSTRY}],
    }


def _input_value(parsed: _Collect, name: str):
    return next(attrs.get("value") for tag, attrs in parsed.attrs if tag == "input" and attrs.get("name") == name)


def test_hostile_story_fields_render_as_text_not_markup():
    parsed = _parse(_render_story_form(_hostile_story()))

    assert not {"script", "img", "iframe", "svg", "b"} & set(parsed.tags)
    assert set(parsed.tags) <= {"div", "form", "label", "input", "textarea", "select", "option", "button"}
    for _tag, attrs in parsed.attrs:
        assert not any(name.startswith("on") for name in attrs), attrs  # no injected event-handler attributes

    # ...and each value survives byte-for-byte as the *text* it was supplied as
    assert _input_value(parsed, "headline") == PAYLOAD_HEADLINE
    assert _input_value(parsed, "source_name") == PAYLOAD_SOURCE_NAME
    assert parsed.textarea["summary"] == PAYLOAD_SUMMARY
    assert parsed.textarea["body"] == PAYLOAD_BODY
    assert parsed.textarea["why_it_matters"] == PAYLOAD_WHY


def test_source_url_keeps_working_as_a_value_when_escaped():
    parsed = _parse(_render_story_form(_hostile_story()))
    assert _input_value(parsed, "source_url") == PAYLOAD_SOURCE_URL  # & and quotes preserved exactly

    ordinary = _parse(_render_story_form({"id": 1, "source_url": "https://news.example/a?x=1&y=2#frag", "industries": []}))
    assert _input_value(ordinary, "source_url") == "https://news.example/a?x=1&y=2#frag"


def test_industry_names_render_as_option_text():
    parsed = _parse(_render_story_form(_hostile_story()))
    assert "b" not in parsed.tags
    assert any(PAYLOAD_INDUSTRY in text for text in parsed.option_text)


def test_a_new_empty_story_renders_empty_fields():
    parsed = _parse(_render_story_form({}))
    assert _input_value(parsed, "headline") == ""
    assert parsed.textarea["summary"] == ""


def test_the_test_can_detect_the_bug_it_guards_against():
    """Control: with escaping replaced by a pass-through, the very same
    payloads DO inject markup -- so the assertions above are meaningful."""
    unescaped = _render_story_form(_hostile_story(), escape_source="function esc(value) { return String(value == null ? '' : value); }")
    parsed = _parse(unescaped)
    assert "script" in parsed.tags or "img" in parsed.tags


def test_no_externally_sourced_field_is_interpolated_raw_anywhere_in_the_page():
    raw_interpolations = [
        "${story.headline", "${story.summary", "${story.source_name", "${story.source_url", "${story.body",
        "${story.why_it_matters", "${u.email}", "${i.name}", "${c.name}", "${group.parent.name}",
        "${g.parent.name}", "${s.story_type_label", "${s.status}",
    ]
    present = [needle for needle in raw_interpolations if needle in ADMIN_HTML]
    assert present == [], f"unescaped interpolation(s) found: {present}"
    assert re.search(r"function esc\(value\)", ADMIN_HTML)
