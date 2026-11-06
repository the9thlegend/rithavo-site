"""
Ritanya Phase A -- the approved product knowledge, as plain data.

Nothing here is generated or learned. Every statement below is taken from
a page or module that already exists in this repository, named in the
`source` field of its entry, so a reviewer can check each claim against its
origin in one diff. If a fact is not on a page, it is not in this file.

Rules this file enforces by construction (and tests/test_ritanya_chat.py
asserts):
  * Every link is a relative path on rithavo.com, chosen from DESTINATIONS.
    No absolute URL appears in any answer, and no other host is ever named.
  * Nothing here describes a personalised result: no score, no fit, no
    recommendation about a person. Ritanya points to the products that
    produce those; she does not produce them.
  * Availability is never asserted. Career Intelligence and Application Diagnostic are described only as "Not yet
    available for purchase." (founder decision, Gate 1.7), with no date and no commitment.
  * Career prices are hidden (founder decision, Gate 1.7). No price text exists in this module.
  * The three pillars are Career, Product Studio and Business Transformation. Product Studio is Career products only;
    technology and automation implementation is not offered. The Business Transformation answers describe only the four
    approved capability areas and the six possible engagement models, and never promise results or timelines.
"""

from typing import NamedTuple

CANONICAL_HOST = "https://rithavo.com"

# Career prices are hidden on every customer-facing surface (founder decision, Gate 1.7), so Ritanya never states a price.
# The flag stays False and is asserted by the tests; there is no price text in this module to switch on.
PRICES_ENABLED = False


class Destination(NamedTuple):
    label: str            # button text
    path: str             # relative, on rithavo.com
    needs_sign_in: bool   # the page is an app page behind sign-in (app-shell.js)


# The ONLY places Ritanya may send a person. Verified against the pages that
# exist in this repo. There is deliberately no "Position" page (none exists
# on rithavo.com) and no Rithavo Card page (the Card is handed off from the
# Home page; its destination is not a rithavo.com page and is never linked).
DESTINATIONS = {
    "home":                  Destination("Go to the Rithavo home page", "/", False),
    "career":                Destination("See Rithavo Career", "/career/", False),
    "about":                 Destination("About Rithavo", "/about/", False),
    "contact":               Destination("Contact Rithavo", "/contact/", False),
    "privacy":               Destination("Read the Privacy page", "/privacy/", False),
    "terms":                 Destination("Read the Terms", "/terms/", False),
    "systems":               Destination("See Product Studio", "/systems/", False),
    "growth":                Destination("See Business Transformation", "/growth/", False),
    "sign_up":               Destination("Join Rithavo", "/sign-up/", False),
    "sign_in":               Destination("Sign in", "/sign-in/", False),
    "career_intelligence":   Destination("Open Career Intelligence", "/career-intelligence/", True),
    "application_diagnosis": Destination("Open Application Diagnostic", "/application-diagnosis/", True),
    "explore":               Destination("Open Explore", "/home/", True),
    "mentorship":            Destination("Open Mentorship", "/mentorship/", True),
    "profile":               Destination("Open your Profile", "/profile/", True),
}

AVAILABILITY_NOTE = (
    "Career Intelligence and Application Diagnostic: Not yet available for purchase. There is no "
    "purchase date to share, and the product pages show the current status."
)

PERSONAL_BOUNDARY = (
    "I can't assess your resume, tell you which career fits you, or give you a score. I don't "
    "have access to your account or your information either."
)


class Topic(NamedTuple):
    id: str
    reply: str
    more: str               # a second, longer answer for "tell me more"
    actions: tuple          # keys of DESTINATIONS, in display order
    suggestions: tuple      # follow-up questions, shown as buttons
    source: str             # where the claim comes from, for review


TOPICS = {t.id: t for t in (
    Topic(
        "overview",
        "Rithavo is a digital products and services company with three pillars. Career is for individuals: "
        "Career Intelligence and Application Diagnostic help you understand where you stand and what to do next. "
        "Product Studio builds Rithavo's own products. Business Transformation works with businesses on how they perform.",
        "Every Rithavo product follows the same shape: a clear look at where things stand, a specific "
        "plan to improve it, and a practical next step. " + AVAILABILITY_NOTE,
        ("career", "about"),
        ("What is Product Studio?", "What is Business Transformation?", "What is Career Intelligence?"),
        "about/index.html; career/index.html; systems/index.html (Product Studio); growth/index.html (Business Transformation)",
    ),
    Topic(
        "ritanya",
        "I'm Ritanya, Rithavo's guide. I answer from Rithavo's approved product information and point "
        "you to the right page. I'm not a live AI, I can't see your account or your information, and "
        "I can't give personal career advice.",
        "That means I can explain what Rithavo's three pillars are, what its products do and how to start a business "
        "enquiry, tell you where things are, and say so plainly when I don't know something.",
        ("career",),
        ("What is Rithavo?", "What can you help me with?", "Which one should I choose?"),
        "design decision, Phase A (no model; fixed information only)",
    ),
    Topic(
        "career_intelligence",
        "Career Intelligence helps you understand whether you're doing well in your current career, "
        "whether you could do better in another company or role, and what opportunities exist across "
        "different roles and industries. It looks at four kinds of move: Stay, New Role, New Industry "
        "and Major Transition.",
        "It is not tied to one job posting; it is a broader read on your direction. It uses your Rithavo "
        "Profile. " + AVAILABILITY_NOTE,
        ("career_intelligence", "career"),
        ("Can I buy it yet?", "What is Application Diagnostic?", "What is a Rithavo Profile?"),
        "index.html (product description, four moves); career/index.html",
    ),
    Topic(
        "application_diagnosis",
        "Application Diagnostic is a one-time diagnosis for a single job opportunity, not a subscription. "
        "Rithavo evaluates the complete job description against your Rithavo Profile and leads to a "
        "tailored resume for that role.",
        "The Career page describes it as six dimensions, scored, with a prioritized fix list. " + AVAILABILITY_NOTE,
        ("application_diagnosis", "career"),
        ("Can I buy it yet?", "What is Career Intelligence?", "What is a Rithavo Profile?"),
        "index.html; about/index.html; career/index.html",
    ),
    Topic(
        "which_product",
        "Choose Application Diagnostic if you have a specific role in front of you and want to know whether "
        "your resume matches it before you apply. Choose Career Intelligence if you're weighing a move, "
        "such as a new role, industry or level, and want a clearer read on where you stand.",
        "I can't tell you which one suits you personally. I don't know your situation, and I can't assess "
        "it. The two descriptions above are the Career page's own guidance.",
        ("career_intelligence", "application_diagnosis"),
        ("What is Career Intelligence?", "What is Application Diagnostic?", "Can I buy it yet?"),
        "career/index.html (\"Choose Application Diagnostic if / Choose Career Intelligence if\")",
    ),
    Topic(
        "profile",
        "Your Rithavo Profile is built from your education and experience. Application Diagnostic and "
        "Career Intelligence both use it, so adding it is the way to get started. You can do that on the "
        "Profile page once you've signed in.",
        "The Rithavo Card, your foundational professional identity built from your Profile, isn't available on rithavo.com yet.",
        ("profile", "sign_up"),
        ("What is a Rithavo Card?", "What is Career Intelligence?", "How do I join?"),
        "home/index.html (profile banner and Card copy)",
    ),
    Topic(
        "card",
        "The Rithavo Card is the foundation of your professional identity on Rithavo, built from your Rithavo "
        "Profile, and completing it is mandatory for every member. It isn't available on rithavo.com yet, so "
        "for now your Profile is where you start: you can add to it and keep it up to date once you've "
        "signed in.",
        "I can't open or create a Card for you, and I can't see whether you have one. Discover and Connect "
        "are planned to build on the Card.",
        ("profile", "explore"),
        ("What are Discover and Connect?", "What is a Rithavo Profile?", "How do I join?"),
        "founder-approved positioning; home/index.html (\"Your Rithavo Card\")",
    ),
    Topic(
        "discover_connect",
        "Discover and Connect are planned parts of Rithavo, and neither is live yet. Discover is meant to help "
        "you find relevant professionals on Rithavo and understand why they are relevant. Connect is meant to "
        "let you request a connection that the other person can accept or decline; once you both accept, you "
        "would exchange professional identities through your Rithavo Cards.",
        "The first version of Connect is focused on professional networking and identity exchange. Both "
        "depend on the Rithavo Card, which isn't available on rithavo.com yet.",
        ("profile", "about"),
        ("What is a Rithavo Card?", "What is a Rithavo Profile?", "How do I join?"),
        "founder-approved positioning (Discover and Connect are planned, not live)",
    ),
    Topic(
        "explore",
        "Explore is continuously refreshed professional and business news. Once you've joined, it shows "
        "stories ordered by how relevant they are to your Rithavo Profile, with the ones relevant to you "
        "first. You can get Explore now by joining.",
        "I can't pick stories for you or tell you what is in the feed today. Explore is on your Home page "
        "after you sign in.",
        ("explore", "sign_up"),
        ("What is Mentorship?", "How do I join?", "What is a Rithavo Profile?"),
        "index.html (\"access to Explore\"); explore-feed.js",
    ),
    Topic(
        "mentorship",
        "Mentorship is 15-minute mentorship sessions. You can discover mentors, or apply to become one; "
        "mentors need at least 10 years of experience in their field.",
        "I can't see who is available or book a session for you. The Mentorship page, after you sign in, "
        "shows mentors and your sessions.",
        ("mentorship", "sign_up"),
        ("What is Explore?", "How do I join?", "What is Career Intelligence?"),
        "mentorship/index.html",
    ),
    Topic(
        "stories",
        "I can't pull up career stories myself. Explore shows professional and business news, and "
        "Mentorship offers 15-minute sessions with experienced mentors, which is the closest thing to "
        "hearing from someone who has made a move.",
        "Both are behind sign-in.",
        ("explore", "mentorship"),
        ("What is Explore?", "What is Mentorship?", "How do I join?"),
        "index.html; mentorship/index.html",
    ),
    Topic(
        "pricing",
        "Prices aren't shown at the moment. Career Intelligence and Application Diagnostic: Not yet available for "
        "purchase. The Career page explains what each one does.",
        AVAILABILITY_NOTE,
        ("career", "contact"),
        ("What is Application Diagnostic?", "What is Career Intelligence?", "How do I contact Rithavo?"),
        "career/index.html; founder decision: Career prices are hidden (Gate 1.7).",
    ),
    Topic(
        "launch",
        AVAILABILITY_NOTE,
        "You can join now to get Explore and to build your Rithavo Profile.",
        ("career", "sign_up"),
        ("How do I join?", "What is Explore?", "What is a Rithavo Profile?"),
        "index.html; career/index.html; contact/index.html; about/index.html",
    ),
    Topic(
        "join",
        "You can join Rithavo now to get access to Explore and to build your Rithavo Profile. "
        "If you already have an account, you can sign in.",
        "I can't create an account or sign you in. The Join and Sign in pages do that.",
        ("sign_up", "sign_in"),
        ("What is Explore?", "What is a Rithavo Profile?", "Can I buy it yet?"),
        "index.html (Join Rithavo; Sign In)",
    ),
    Topic(
        "product_studio",
        "Product Studio is where Rithavo builds its own products. Today it includes two products from Rithavo "
        "Career, Career Intelligence and Application Diagnostic. Status: Not yet available for purchase.",
        "Product Studio builds Rithavo's own products. Business Transformation is separate: it works with businesses "
        "on how they perform. This part of the site was previously called Systems.",
        ("systems", "career"),
        ("What is Business Transformation?", "What is Career Intelligence?", "What is Application Diagnostic?"),
        "systems/index.html (Product Studio); Gate 1.7: Product Studio is Career products only for now",
    ),
    Topic(
        "business_transformation",
        "Business Transformation is where Rithavo works with businesses to strengthen operations, support delivery "
        "commitments and pursue growth opportunities. Four areas are open for discussion: sales and revenue "
        "operations; business process and operational management; taking ownership of selected business functions; "
        "and business development and revenue growth. Rithavo does not promise guaranteed growth or standard "
        "performance outcomes.",
        "Sales and revenue operations covers MQL and SQL lead requirements, lead research and review of the data you "
        "provide. Business process and operational management covers organisational structures, hierarchy, business "
        "processes, Standard Operating Procedures and operational strategies. Taking ownership can include additional "
        "resources, support for an existing production team, or operational control of an agreed production team. "
        "Business development and revenue growth can include performance-linked objectives agreed separately.",
        ("growth", "contact"),
        ("What are the engagement models?", "How do I start a business enquiry?", "What is Product Studio?"),
        "growth/index.html (Business Transformation); Gate 1.7: four approved capabilities",
    ),
    Topic(
        "engagement_models",
        "Depending on your requirement, an engagement may be advisory or consultation; a defined project with agreed "
        "deliverables; ongoing operational support; dedicated resources or team deployment; end-to-end ownership of "
        "an agreed business function; or a performance-linked engagement with agreed targets.",
        "These are possible engagement models, not standard packages. The right one is discussed once Rithavo "
        "understands your requirement, and the scope, terms and timelines are agreed separately.",
        ("growth", "contact"),
        ("What is Business Transformation?", "How do I start a business enquiry?", "Who approves the scope of work?"),
        "growth/index.html (Business Transformation); Gate 1.7: six possible engagement models",
    ),
    Topic(
        "business_enquiry",
        "To start, email hello@rithavo.com and describe what you are looking to achieve. Your enquiry will be "
        "reviewed by Rithavo. If appropriate, a Rithavo specialist will get in touch to understand your requirements. "
        "Any scope of work is approved by Rithavo's Founder before it is agreed.",
        "A specialist can discuss your objectives and existing commitments, but nothing is committed until the scope "
        "has been approved by the Founder and both sides agree. No response time is promised.",
        ("contact", "growth"),
        ("What are the engagement models?", "What is Business Transformation?", "How do I contact Rithavo?"),
        "contact/index.html; growth/index.html; Gate 1.7: enquiry wording and Founder approval of scope",
    ),
    Topic(
        "technology_services",
        "Right now, Product Studio builds Rithavo's own products, which today are Career Intelligence and Application "
        "Diagnostic. Technology and automation implementation isn't part of what Rithavo offers at the moment. If you "
        "have a business requirement, Business Transformation and the enquiry route are the best next step.",
        "If your need is about how a business operates or performs, such as sales operations, processes or delivery, "
        "Business Transformation is the place to start.",
        ("growth", "contact"),
        ("What is Product Studio?", "What is Business Transformation?", "How do I start a business enquiry?"),
        "Gate 1.7: technology and automation implementation are not part of the current offering",
    ),
    Topic(
        "contact",
        "The fastest way to reach Rithavo is email: hello@rithavo.com. That covers questions about the products, "
        "orders and billing, business requirements and feedback. For refunds, see the Terms first.",
        "I can't send a message for you.",
        ("contact", "terms"),
        ("What is Rithavo?", "How much does it cost?", "How do I join?"),
        "contact/index.html",
    ),
    Topic(
        "privacy",
        "I answer from fixed information, and your messages here are not sent to an AI service. For how "
        "Rithavo handles your information overall, the Privacy page is the authority, and I'm not a "
        "substitute for it.",
        "I can't see your account or your Rithavo information.",
        ("privacy", "terms"),
        ("What can you help me with?", "How do I contact Rithavo?", "What is Rithavo?"),
        "privacy/index.html; this module (Phase A uses no provider)",
    ),
    Topic(
        "method",
        "Every Rithavo product follows the same shape: a clear look at where things stand, a specific plan "
        "to improve it, and a practical next step, not a guess dressed up as advice. Rithavo's scores "
        "reflect its own methodology and are not affiliated with, certified by, or representative of any "
        "employer's actual ATS software.",
        "That is how the About and Career pages describe it.",
        ("about", "career"),
        ("What is Career Intelligence?", "What is Application Diagnostic?", "What is Rithavo?"),
        "about/index.html; footer disclaimer on career/index.html",
    ),
)}

# --- answers that are not topics -------------------------------------------------

REFUSE_PERSONAL = (
    PERSONAL_BOUNDARY + " That is what Career Intelligence and Application Diagnostic are for, and I can "
    "point you to them."
)
REFUSE_PERSONAL_ACTIONS = ("career_intelligence", "application_diagnosis")
REFUSE_PERSONAL_SUGGESTIONS = ("What is Career Intelligence?", "What is Application Diagnostic?",
                               "Which one should I choose?")

NO_ACCOUNT_ACCESS = (
    "I can't see your account or any of your information, so I can't look that up for you. Once you're signed in, "
    "your Profile page shows what you've added, and Rithavo's team can help with anything about your account."
)
NO_ACCOUNT_ACCESS_ACTIONS = ("profile", "contact")

REFUSE_OUT_OF_BOUNDS = (
    "I can only answer from Rithavo's product information, and I can't change how I work, share my "
    "instructions, or send you anywhere outside Rithavo. I can help with what Rithavo is, what its "
    "products do, and where to find things."
)

GREETING = "Hello. I can tell you about Rithavo and where to find things. What would you like to know?"
THANKS = "Anytime."
CLARIFY = (
    "Happy to help. Are you trying to understand your overall career direction, or to check a resume "
    "against one specific job?"
)
CLARIFY_SUGGESTIONS = ("Understand my career direction", "Check a resume against a job",
                       "Tell me about Rithavo")

UNKNOWN = (
    "I don't have information about that. I can only answer from Rithavo's approved product "
    "information, and I'd rather say so than guess."
)
UNKNOWN_SUGGESTIONS = ("What is Rithavo?", "What is Career Intelligence?", "What is Application Diagnostic?",
                       "How do I contact Rithavo?")
UNKNOWN_REPEATED = (
    UNKNOWN + " If it's something specific, emailing hello@rithavo.com is the surest way to get an answer."
)

NO_TOPIC_FOR_FOLLOWUP = "Which one do you mean? I can tell you about any of these."
START_SUGGESTIONS = ("What is Rithavo?", "What is Career Intelligence?", "What is Application Diagnostic?",
                     "Which one should I choose?")

# Messages the unavailable / rate-limited states show. Kept here so the wording is reviewed with the rest.
BUSY = "I'm getting a lot of questions right now. Please try again in a minute."
UNAVAILABLE = "I can't answer right now. Your conversation is kept. The links below still work."
FALLBACK_ACTIONS = ("career", "contact", "about")


# One line the panel always shows, so nobody mistakes the assistant for live AI.
# (The browser-engine variant adds one true sentence: in that mode nothing a visitor types is sent anywhere.)
DISCLOSURE = (
    "I answer from Rithavo's approved product information. I'm not a live AI, I can't see your account, "
    "and I can't give personal career advice."
)

DISCLOSURE_BROWSER = DISCLOSURE + " Your messages are answered in your browser and are not sent to Rithavo or anyone else."

# Starter questions by page, so the guide is useful where the person is. Every phrase here is asked of the
# real matcher in tests/test_ritanya_chat.py and must produce a reviewed topic answer, never "I don't know".
PAGE_STARTERS = {
    "/":                                       START_SUGGESTIONS,
    "/career/":                                ("What is Career Intelligence?", "What is Application Diagnostic?", "Which one should I choose?"),
    "/career/career-intelligence/":            ("What is Career Intelligence?", "Which one should I choose?", "Can I buy it yet?"),
    "/career/application-diagnostic/":         ("What is Application Diagnostic?", "Which one should I choose?", "Can I buy it yet?"),
    "/career-intelligence/":                   ("What is Career Intelligence?", "What is a Rithavo Profile?", "Can I buy it yet?"),
    "/application-diagnosis/":                 ("What is Application Diagnostic?", "What is a Rithavo Profile?", "Can I buy it yet?"),
    "/home/":                                  ("What is Explore?", "What is Mentorship?", "What is a Rithavo Profile?"),
    "/profile/":                               ("What is a Rithavo Profile?", "What is a Rithavo Card?", "What is Career Intelligence?"),
    "/resumes/":                               ("What is Application Diagnostic?", "What is a Rithavo Profile?"),
    "/history/":                               ("What is Application Diagnostic?", "What is Career Intelligence?"),
    "/mentorship/":                            ("What is Mentorship?", "What is Explore?"),
    "/systems/":                               ("What is Product Studio?", "What is Business Transformation?", "What is Rithavo?"),
    "/growth/":                                ("What is Business Transformation?", "What are the engagement models?", "How do I start a business enquiry?"),
    "/about/":                                 ("What is Rithavo?", "How does Rithavo work?", "How do I join?"),
    "/contact/":                               ("How do I contact Rithavo?", "How do I start a business enquiry?", "What is Rithavo?"),
    "/privacy/":                               ("How does Rithavo handle my data?", "What can you help me with?"),
    "/terms/":                                 ("How do I contact Rithavo?", "What is Rithavo?"),
    "/account/":                               ("What can you help me with?", "How do I contact Rithavo?"),
}
