"""
Founder-approved positioning, kept honest.

Rithavo Card: the foundational professional identity built from the Profile; completing it is mandatory for every member.
Discover: find relevant professionals and understand why they are relevant.
Connect: request a connection, the other person accepts or declines, then identities are exchanged through Rithavo Cards.

None of the three is available on rithavo.com today, so every public statement must say so, must not point to another host,
and must not describe unrestricted messaging.
"""

import re
from pathlib import Path

from app import ritanya_knowledge as K

ROOT = Path(__file__).resolve().parent.parent


def _journey_steps():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    steps = re.findall(r'<div class="journey-step">(.*?)</div>\s*(?=<div class="journey-step">|</div>\s*<)', html, re.S)
    return {re.search(r'journey-eyebrow">([^<]+)<', s).group(1): re.sub(r"<[^>]+>", " ", s) for s in steps}


def test_home_page_journey_marks_card_discover_and_connect_as_planned():
    steps = _journey_steps()
    for name in ("Represent", "Discover", "Connect"):
        text = re.sub(r"\s+", " ", steps[name])
        assert "Planned; not yet available" in text, (name, text)
    represent = re.sub(r"\s+", " ", steps["Represent"])
    assert "mandatory for every member" in represent and "built from your Profile" in represent
    connect = re.sub(r"\s+", " ", steps["Connect"])
    assert "accepts or declines" in connect and "Rithavo Cards" in connect
    assert not re.search(r"messag|chat|inbox|dm\b", " ".join(steps.values()), re.I), "no messaging may be promised"


def test_hero_graphic_does_not_claim_a_shareable_card():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "Ready to share" not in html


def test_ritanya_says_discover_and_connect_are_planned_and_not_live():
    t = K.TOPICS["discover_connect"]
    assert "neither is live yet" in t.reply
    assert "accept or decline" in t.reply and "Rithavo Cards" in t.reply
    assert "networking and identity exchange" in t.more
    assert not re.search(r"messag|chat|inbox|\bdm\b", t.reply + t.more, re.I)
    assert not re.search(r"(?<!neither )\bis live\b(?! yet)|available now|available today|you can (discover|connect|request)", t.reply + t.more, re.I)


def test_systems_and_growth_name_their_planned_capabilities_and_stay_in_development():
    systems = (ROOT / "systems" / "index.html").read_text(encoding="utf-8")
    growth = (ROOT / "growth" / "index.html").read_text(encoding="utf-8")
    assert "productivity templates" in systems and "digital toolkits and workflows" in systems
    assert "lead generation" in growth
    assert "isn't live yet" in systems and "isn't live yet" in growth
    assert "productivity templates" in K.TOPICS["systems_growth"].reply and "lead generation" in K.TOPICS["systems_growth"].reply
