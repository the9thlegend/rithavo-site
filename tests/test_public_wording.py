"""
Public wording gates (customer-facing pages only):

  * Rithavo does not say anything is live or active today. Career Intelligence and Application Diagnosis are
    "available for purchase at launch"; Systems and Growth are in development. The pages must not contradict each other.
  * No page names another Rithavo host (app.rithavo.com and the like) in visible text.
  * The Card is not promised on the contact page.
"""

import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INTERNAL = {"design-lab", "ritanya-handoff", "tests", "app", "api", "tools", "node_modules", "admin", "offline", ".git", "storage"}
PAGES = [p for p in sorted(ROOT.rglob("index.html")) if p.relative_to(ROOT).parts[0] not in INTERNAL]


def visible(path):
    raw = path.read_text(encoding="utf-8")
    raw = re.sub(r"<(script|style)\b.*?</\1>", " ", raw, flags=re.S)
    raw = re.sub(r"<!--.*?-->", " ", raw, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()


CONTRADICTIONS = re.compile(
    r"\blive now\b|\bactive today\b|\bboth active\b|\bavailable now\b|\bnow available\b|\bnow live\b|\bbuy now\b|\border now\b|\bpurchase now\b",
    re.I)
OTHER_HOST = re.compile(r"\b(?:[a-z0-9-]+\.rithavo\.com|rithavo\.app|rithavo\.applications\.com)\b", re.I)


def test_there_are_pages_to_check():
    assert len(PAGES) >= 18


def test_no_page_says_a_product_is_live_or_active_today():
    bad = {p.relative_to(ROOT).as_posix(): CONTRADICTIONS.findall(visible(p)) for p in PAGES if CONTRADICTIONS.search(visible(p))}
    assert bad == {}


def test_the_about_page_matches_the_other_pages_on_availability():
    about = visible(ROOT / "about" / "index.html")
    assert "available for purchase at launch" in about
    assert "Rithavo Career, available for purchase at launch" in about


def test_no_page_names_another_rithavo_host_in_visible_text():
    bad = {p.relative_to(ROOT).as_posix(): OTHER_HOST.findall(visible(p)) for p in PAGES if OTHER_HOST.search(visible(p))}
    assert bad == {}


def test_the_contact_page_does_not_promise_the_card():
    contact = visible(ROOT / "contact" / "index.html")
    assert "Card" not in contact and "your Rithavo account or Profile" in contact
