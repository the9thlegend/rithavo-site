"""
Three-pillar positioning (Gate 1.7): Career, Product Studio and Business Transformation, on the existing routes.

Each test verifies one approved change:
  * /systems/ is Product Studio and /growth/ is Business Transformation, in place (no new routes, no redirects);
  * Career prices are hidden everywhere a customer can see or fetch them, while the stored records are untouched;
  * the only availability wording is "Not yet available for purchase.";
  * the product is named "Application Diagnostic" in everything a customer reads;
  * canonical / noindex / sitemap / robots;
  * the changed scripts and stylesheet carry one cache-busting stamp, so an installed app fetches them on its first load;
  * Ritanya answers the new pillar questions and no longer says anything the pages no longer say.
"""

import json
import re
from pathlib import Path
from xml.etree import ElementTree

import pytest

import config
from app import ritanya_chat as rc
from app import ritanya_knowledge as K
from tests.conftest import login_via_magic_link
from tests.test_public_wording import PAGES, visible

ROOT = Path(__file__).resolve().parent.parent
ASSET_VERSION = "20261106"
ENQUIRY_SENTENCE = "Your enquiry will be reviewed by Rithavo. If appropriate, a Rithavo specialist will get in touch to understand your requirements."
STATUS = "Not yet available for purchase."
CONTENT_PAGES = {
    "index.html": "/", "about/index.html": "/about/", "contact/index.html": "/contact/", "career/index.html": "/career/",
    "career/career-intelligence/index.html": "/career/career-intelligence/",
    "career/application-diagnostic/index.html": "/career/application-diagnostic/",
    "career/career-intelligence/demo/index.html": "/career/career-intelligence/demo/",
    "systems/index.html": "/systems/", "growth/index.html": "/growth/", "privacy/index.html": "/privacy/", "terms/index.html": "/terms/",
}


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def html_files():
    return [p for p in ROOT.rglob("*.html")
            if p.relative_to(ROOT).parts[0] not in {"design-lab", "ritanya-handoff", "tests", "node_modules", ".git"}]


# Everything a customer's browser can receive from this repository (the admin page is internal and shows launch status to the owner).
def customer_files():
    return [p for p in html_files() if p.relative_to(ROOT).parts[0] != "admin"] + [ROOT / "app-shell.js", ROOT / "pwa.js", ROOT / "manifest.webmanifest"]


# ------------------------------------------------------------------ Product Studio and Business Transformation, in place

def test_the_pillar_routes_are_the_existing_ones_with_no_new_routes_and_no_redirects():
    assert (ROOT / "systems" / "index.html").is_file() and (ROOT / "growth" / "index.html").is_file()
    assert not (ROOT / "product-studio").exists() and not (ROOT / "business-transformation").exists()
    vercel = json.loads(read("vercel.json"))
    assert not [r for r in vercel["routes"] if "systems" in json.dumps(r) or "growth" in json.dumps(r)]   # no redirects for the pillar routes


def test_systems_is_product_studio_career_products_only():
    page = visible(ROOT / "systems" / "index.html")
    assert "Product Studio" in page and "Building products that solve real problems" in page
    assert "Talk to Rithavo about Product Studio" in page and ENQUIRY_SENTENCE in page
    assert 'href="/contact/">Talk to Rithavo about Product Studio' in read("systems/index.html")
    for product in ("Career Intelligence", "Application Diagnostic"):
        assert product in page
    assert page.count(STATUS) == 3        # the site banner and the two product cards
    assert not re.search(r"Rithavo Systems|automation|custom software|integration|dashboard|productivity templates|toolkit|small teams", page, re.I)


def test_growth_is_business_transformation_with_the_four_capabilities_and_six_models():
    page = visible(ROOT / "growth" / "index.html")
    assert "Turning business potential into measurable performance" in page
    for capability in ("Sales and revenue operations", "Business process and operational management",
                       "Taking ownership of selected business functions", "Business development and revenue growth"):
        assert capability in page
    for model in ("Advisory or consultation", "A defined project with agreed deliverables", "Ongoing operational support",
                  "Dedicated resources or team deployment", "End-to-end ownership of an agreed business function",
                  "A performance-linked engagement with agreed targets"):
        assert model in page
    assert "not standard packages" in page and "Discuss Your Business Requirements" in page and ENQUIRY_SENTENCE in page
    assert "approved by Rithavo's Founder" in page
    # nothing unapproved: no technology/automation/dashboard offer, no guarantee other than the sentence that denies one, no figures, no times
    assert not re.search(r"Rithavo Growth|automation|technology implementation|dashboard|marketing, content|₹|\d\s?%|within \d+ (day|hour|week)|24.48", page, re.I)
    assert re.findall(r"guarantee\w*", page, re.I) == ["guaranteed"] and "does not promise guaranteed growth" in page


def test_navigation_uses_the_new_pillar_names_everywhere():
    for page in html_files():
        text = page.read_text(encoding="utf-8")
        assert not re.search(r">Systems<|>Growth<|Rithavo Systems|Rithavo Growth", text), page
        if '<a href="/systems/">' in text:
            assert '<a href="/systems/">Product Studio</a>' in text and '<a href="/growth/">Business Transformation</a>' in text, page
    pwa = read("pwa.js")
    assert 'link("systems", "Studio", "/systems/")' in pwa and 'link("growth", "Transform", "/growth/")' in pwa
    assert "In development" not in pwa and "Systems" not in pwa and "Growth" not in pwa


def test_the_homepage_presents_the_three_pillars_and_the_brand_positioning():
    home = visible(ROOT / "index.html")
    assert "Understand. Explore. Connect. Grow." in home
    for card in ("Explore Career", "Explore Product Studio", "Explore Business Transformation", "Work With Rithavo"):
        assert card in home
    raw = read("index.html")
    assert 'href="/systems/" style="width:fit-content;">Explore Product Studio' in raw and 'href="/growth/" style="width:fit-content;">Explore Business Transformation' in raw


# ------------------------------------------------------------------ Career: price, availability, name

def test_no_career_price_is_visible_anywhere_a_customer_can_see_it():
    for path in customer_files():
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"₹\s*\d|Launch price|launch price|\b399\b|\b799\b", text), path.relative_to(ROOT)
    assert "₹" not in read("history/index.html")


def test_no_page_makes_a_launch_or_availability_commitment():
    forbidden = re.compile(r"launching soon|available at launch|available for purchase at launch|will be available|launch day|becomes purchasable|"
                           r"moment it's available|coming soon|ahead of launch|wait for launch", re.I)
    for path in customer_files():
        assert not forbidden.search(path.read_text(encoding="utf-8")), path.relative_to(ROOT)


def test_the_approved_availability_wording_is_on_the_career_surfaces():
    for rel in ("career/index.html", "career/career-intelligence/index.html", "career/application-diagnostic/index.html",
                "about/index.html", "index.html", "career-intelligence/index.html", "application-diagnosis/index.html"):
        assert STATUS in visible(ROOT / rel) or STATUS in read(rel), rel
    for rel in ("career/career-intelligence/index.html", "career/application-diagnostic/index.html"):
        # the status is a label, not a button that leads to a purchase page
        assert 'class="btn btn-primary" href="/career-intelligence/"' not in read(rel) and 'href="/application-diagnosis/">Not yet' not in read(rel)
    for path in html_files():
        if path.relative_to(ROOT).parts[0] != "admin":
            assert "Career Intelligence and Application Diagnostic: Not yet available for purchase." in path.read_text(encoding="utf-8") or "prelaunch-banner" not in path.read_text(encoding="utf-8"), path


def test_the_product_is_named_application_diagnostic_wherever_a_customer_reads_it():
    for path in customer_files():
        assert not re.search(r"Application Diagnos(is|es)", path.read_text(encoding="utf-8")), path.relative_to(ROOT)
    # the signed-in route and the internal identifiers are deliberately unchanged
    assert (ROOT / "application-diagnosis" / "index.html").is_file()
    assert '"url": "/application-diagnosis/?source=pwa-shortcut"' in read("manifest.webmanifest")
    for text in [t.reply + t.more for t in K.TOPICS.values()] + [d.label for d in K.DESTINATIONS.values()] + [s for page in K.PAGE_STARTERS.values() for s in page]:
        assert "Application Diagnosis" not in text


# ------------------------------------------------------------------ the price APIs and the customer views of stored records

def test_the_price_endpoints_return_no_amount_while_purchases_are_locked(app_and_client, monkeypatch):
    app, client = app_and_client
    monkeypatch.setattr(config, "LAUNCH_DATE", None)
    login_via_magic_link(client, app, "pillars-price-locked@example.com")
    gateway = app.state.payment_gateway.name
    assert client.get("/career-intelligence/price").json() == {"launch_locked": True, "gateway": gateway}
    assert client.get("/diagnosis/price").json() == {"launch_locked": True, "gateway": gateway}


def test_the_price_endpoints_still_serve_the_purchase_flow_once_purchases_are_separately_enabled(app_and_client):
    app, client = app_and_client                       # the suite default: a past launch date, i.e. unlocked
    login_via_magic_link(client, app, "pillars-price-unlocked@example.com")
    ci, ad = client.get("/career-intelligence/price").json(), client.get("/diagnosis/price").json()
    assert ci["launch_locked"] is False and ci["amount_inr"] == 799
    assert ad["launch_locked"] is False and "amount_inr" in ad and "qualifying_count" in ad


def test_customer_views_of_purchases_and_entitlements_drop_the_amount_but_the_stored_rows_keep_it(app_and_client, db):
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "pillars-history@example.com")
    purchase = client.post("/career-intelligence/purchase", json={}).json()
    assert client.get("/purchases").json() and all("amount_inr" not in row for row in client.get("/purchases").json())
    assert "amount_inr" not in client.get(f"/purchases/{purchase['purchase_id']}").json()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO entitlements (user_id, product, status, amount, currency, payment_source, external_payment_reference, purchased_at, activated_at) "
            "VALUES (?, 'career_intelligence', 'ACTIVE', 799, 'INR', 'rithavo_web_gateway', 'pillars-ref', ?, ?)",
            (user_id, "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"))
        entitlement_id = conn.execute("SELECT id FROM entitlements WHERE external_payment_reference = 'pillars-ref'").fetchone()["id"]
    assert "amount" not in client.get(f"/entitlements/{entitlement_id}").json()
    # historical records are intact: nothing was deleted or altered
    with db.connect() as conn:
        assert conn.execute("SELECT amount_inr FROM purchases WHERE id = ?", (purchase["purchase_id"],)).fetchone()["amount_inr"] == 799
        assert conn.execute("SELECT amount FROM entitlements WHERE id = ?", (entitlement_id,)).fetchone()["amount"] == 799


# ------------------------------------------------------------------ SEO and structure

def test_every_page_is_either_canonical_on_rithavo_com_or_noindex():
    for path in html_files():
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        canon = re.findall(r'<link rel="canonical" href="([^"]+)">', text)
        noindex = 'name="robots" content="noindex"' in text
        if rel in CONTENT_PAGES:
            assert canon == ["https://rithavo.com" + CONTENT_PAGES[rel]] and not noindex, rel
        else:
            assert canon == [] and noindex, rel


def test_sitemap_and_robots_list_only_the_canonical_urls_and_hide_the_private_areas():
    tree = ElementTree.fromstring(read("sitemap.xml"))
    urls = [e.text for e in tree.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
    assert sorted(urls) == sorted("https://rithavo.com" + p for p in CONTENT_PAGES.values())
    robots = read("robots.txt")
    assert "Sitemap: https://rithavo.com/sitemap.xml" in robots and "Allow: /" in robots
    for private in ("/api/", "/admin/", "/home/", "/account/", "/profile/", "/history/", "/resumes/", "/sign-in/", "/sign-up/",
                    "/career-intelligence/", "/application-diagnosis/"):
        assert f"Disallow: {private}" in robots
    assert "Disallow: /career/" not in robots and "Disallow: /systems/" not in robots and "Disallow: /growth/" not in robots


def test_no_customer_page_names_a_host_other_than_rithavo_com():
    for path in customer_files():
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"https?://(?!rithavo\.com|www\.w3\.org|vercel\.live)[a-z0-9.-]*rithavo", text), path.relative_to(ROOT)
        assert "app.rithavo.com" not in text and "application.rithavo.com" not in text, path.relative_to(ROOT)


def test_the_changed_assets_carry_one_cache_busting_stamp_on_every_page():
    pattern = re.compile(r'(?:href|src)="(/styles\.css|/pwa\.js|/app-shell\.js|/assets/ritanya/ritanya\.js)(\?v=[0-9]+)?"')
    seen = 0
    for path in html_files():
        for asset, stamp in pattern.findall(path.read_text(encoding="utf-8")):
            seen += 1
            assert stamp == f"?v={ASSET_VERSION}", (path.relative_to(ROOT), asset, stamp)
    assert seen >= 70
    # the service worker itself is unchanged: it still precaches the unstamped shell and serves assets stale-while-revalidate,
    # which is exactly why a changed stamp (a different URL) is what makes an installed app fetch the new file immediately
    assert 'const VERSION = "v2";' in read("sw.js")


# ------------------------------------------------------------------ Ritanya

def answer(text):
    return rc.answer(text)


def test_ritanya_explains_product_studio_and_the_products_in_it():
    r = answer("What is Product Studio?")
    assert r["topic"] == "product_studio" and not r["unknown"]
    assert "Career Intelligence" in r["reply"] and "Application Diagnostic" in r["reply"] and STATUS in r["reply"]
    assert answer("Is Rithavo Systems live?")["topic"] == "product_studio"          # the old page name still reaches the right answer


def test_ritanya_explains_business_transformation_without_guaranteeing_anything():
    r = answer("Tell me about Business Transformation")
    assert r["topic"] == "business_transformation"
    for part in ("sales and revenue operations", "business process and operational management",
                 "taking ownership of selected business functions", "business development and revenue growth"):
        assert part in r["reply"]
    assert "does not promise guaranteed growth" in r["reply"]
    g = answer("Do you guarantee revenue growth?")
    assert g["topic"] == "business_transformation" and "does not promise guaranteed growth" in g["reply"]
    assert answer("Can you help with sales and revenue operations?")["topic"] == "business_transformation"
    detail = rc.answer("tell me more", last_topic="business_transformation")["reply"]
    assert "MQL and SQL" in detail and "organisational structures" in detail and "operational control of an agreed production team" in detail


def test_ritanya_lists_the_six_engagement_models_and_says_they_are_not_packages():
    r = answer("What are the engagement models?")
    assert r["topic"] == "engagement_models"
    for model in ("advisory or consultation", "defined project with agreed deliverables", "ongoing operational support",
                  "dedicated resources or team deployment", "end-to-end ownership of an agreed business function",
                  "performance-linked engagement with agreed targets"):
        assert model in r["reply"]
    assert "not standard packages" in rc.answer("tell me more", last_topic="engagement_models")["reply"]


def test_ritanya_explains_how_to_start_an_enquiry_and_that_the_founder_approves_scope():
    for question in ("How do I start a business enquiry?", "Who approves the scope of work?"):
        r = answer(question)
        assert r["topic"] == "business_enquiry"
        assert ENQUIRY_SENTENCE in r["reply"] and "approved by Rithavo's Founder" in r["reply"]
    assert "No response time is promised" in rc.answer("tell me more", last_topic="business_enquiry")["reply"]


def test_ritanya_is_honest_that_technology_and_automation_implementation_is_not_offered():
    for question in ("I want to build custom software", "Can you automate our workflows?"):
        r = answer(question)
        assert r["topic"] == "technology_services" and "isn't part of what Rithavo offers" in r["reply"]


def test_ritanya_never_states_a_price_or_a_launch_commitment():
    for question in ("How much does it cost?", "What is the price of Career Intelligence?", "Is it free?", "Can I buy it yet?", "When does it launch?"):
        reply = answer(question)["reply"]
        assert not re.search(r"₹|\d", reply), (question, reply)
        assert not re.search(r"at launch|launching|launch date|launch day|will be available", reply, re.I), (question, reply)
    assert STATUS in answer("How much does it cost?")["reply"] and STATUS in answer("Can I buy it yet?")["reply"]


def test_nothing_ritanya_says_or_ships_still_describes_the_old_pillars_or_the_old_availability():
    blob = " ".join(t.reply + " " + t.more + " " + " ".join(t.suggestions) for t in K.TOPICS.values())
    blob += " " + " ".join(s for page in K.PAGE_STARTERS.values() for s in page)
    assert not re.search(r"Systems and Growth|Rithavo Systems|Rithavo Growth|productivity templates|small teams|ahead of launch|at launch", blob)
    engine = read("assets/ritanya/engine.js")
    assert "Application Diagnosis" not in engine and "Systems and Growth" not in engine and "available for purchase at launch" not in engine


def test_the_starter_questions_on_the_pillar_pages_are_answered_by_reviewed_topics():
    for page in ("/systems/", "/growth/", "/contact/"):
        for question in K.PAGE_STARTERS[page]:
            r = answer(question)
            assert not r["unknown"] and not r["refused"], (page, question)
