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
  * Availability is never asserted. The public pages now agree ("available
    for purchase at launch"; the About page's former "live now" was corrected),
    and every answer still defers to the product pages for what is open today.
"""

from typing import NamedTuple

CANONICAL_HOST = "https://rithavo.com"

# Prices appear on the public Career page ("Launch price" 399 and 799), but
# whether Ritanya may state them is a founder decision that is still open.
# False = she points to the Career page instead. One flag, no other change.
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
    "systems":               Destination("See Rithavo Systems", "/systems/", False),
    "growth":                Destination("See Rithavo Growth", "/growth/", False),
    "sign_up":               Destination("Join Rithavo", "/sign-up/", False),
    "sign_in":               Destination("Sign in", "/sign-in/", False),
    "career_intelligence":   Destination("Open Career Intelligence", "/career-intelligence/", True),
    "application_diagnosis": Destination("Open Application Diagnosis", "/application-diagnosis/", True),
    "explore":               Destination("Open Explore", "/home/", True),
    "mentorship":            Destination("Open Mentorship", "/mentorship/", True),
    "profile":               Destination("Open your Profile", "/profile/", True),
}

AVAILABILITY_NOTE = (
    "Rithavo's public pages say Career Intelligence and Application Diagnosis will be available "
    "for purchase at launch. I don't have a launch date and I can't see what is open today, so "
    "the product pages are the place to check."
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
        "Rithavo is a digital products and services company that builds structured, evidence-based "
        "tools to show people where they stand and what to do next. Its first area is Rithavo Career, "
        "with two products: Career Intelligence and Application Diagnosis. Rithavo Systems and Rithavo "
        "Growth are still in development.",
        "Every Rithavo product follows the same shape: a clear look at where things stand, a specific "
        "plan to improve it, and a practical next step. " + AVAILABILITY_NOTE,
        ("career", "about"),
        ("What is Career Intelligence?", "What is Application Diagnosis?", "Which one should I choose?"),
        "about/index.html; career/index.html; systems/index.html; growth/index.html",
    ),
    Topic(
        "ritanya",
        "I'm Ritanya, Rithavo's guide. I answer from Rithavo's approved product information and point "
        "you to the right page. I'm not a live AI, I can't see your account or your information, and "
        "I can't give personal career advice.",
        "That means I can explain what Rithavo's products are, tell you where things are, and say so "
        "plainly when I don't know something.",
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
        ("How much does it cost?", "What is Application Diagnosis?", "What is a Rithavo Profile?"),
        "index.html (product description, four moves); career/index.html",
    ),
    Topic(
        "application_diagnosis",
        "Application Diagnosis is a one-time diagnosis for a single job opportunity, not a subscription. "
        "Rithavo evaluates the complete job description against your Rithavo Profile and leads to a "
        "tailored resume for that role.",
        "The Career page describes it as six dimensions, scored, with a prioritized fix list. Returning "
        "customers get lower pricing on every purchase after the first. " + AVAILABILITY_NOTE,
        ("application_diagnosis", "career"),
        ("How much does it cost?", "What is Career Intelligence?", "What is a Rithavo Profile?"),
        "index.html; about/index.html; career/index.html",
    ),
    Topic(
        "which_product",
        "Choose Application Diagnosis if you have a specific role in front of you and want to know whether "
        "your resume matches it before you apply. Choose Career Intelligence if you're weighing a move, "
        "such as a new role, industry or level, and want a clearer read on where you stand.",
        "I can't tell you which one suits you personally. I don't know your situation, and I can't assess "
        "it. The two descriptions above are the Career page's own guidance.",
        ("career_intelligence", "application_diagnosis"),
        ("What is Career Intelligence?", "What is Application Diagnosis?", "How much does it cost?"),
        "career/index.html (\"Choose Application Diagnostic if / Choose Career Intelligence if\")",
    ),
    Topic(
        "profile",
        "Your Rithavo Profile is built from your education and experience. Application Diagnosis and "
        "Career Intelligence both use it, so adding it is the way to get started. You can do that on the "
        "Profile page once you've signed in.",
        "Your Rithavo Card is the polished, shareable version of your Profile.",
        ("profile", "sign_up"),
        ("What is a Rithavo Card?", "What is Career Intelligence?", "How do I join?"),
        "home/index.html (profile banner and Card copy)",
    ),
    Topic(
        "card",
        "Your Rithavo Profile is the foundation, and your Rithavo Card is the polished, shareable version "
        "of it. When you use Rithavo in a web browser and have a Profile, your Home page is where you "
        "generate or view your Card. It isn't available inside the installed app.",
        "I can't open or create a Card for you, and I can't see whether you have one.",
        ("explore", "profile"),
        ("What is a Rithavo Profile?", "How do I join?", "What is Explore?"),
        "home/index.html (\"Your Rithavo Card\")",
    ),
    Topic(
        "explore",
        "Explore is continuously refreshed professional and business news. Once you've joined, it shows "
        "stories ordered by how relevant they are to your Rithavo Profile, with the ones relevant to you "
        "first. You can get Explore now by joining, ahead of launch.",
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
        ("Launch prices on the Career page: Application Diagnosis ₹399 for a first purchase, with lower "
         "pricing for returning customers, and Career Intelligence ₹799 one-time. "
         if PRICES_ENABLED else
         "Prices are listed on the Career page, so that is the reliable place to see them. ")
        + "I can't take a payment or tell you what is open to buy today.",
        AVAILABILITY_NOTE,
        ("career", "terms"),
        ("What is Application Diagnosis?", "What is Career Intelligence?", "How do I contact Rithavo?"),
        "career/index.html (\"Launch price\"); app/pricing.py. Stating prices is gated by PRICES_ENABLED.",
    ),
    Topic(
        "launch",
        AVAILABILITY_NOTE,
        "You can join now to get Explore and to build your Rithavo Profile ahead of launch.",
        ("career", "sign_up"),
        ("How do I join?", "What is Explore?", "What is a Rithavo Profile?"),
        "index.html; career/index.html; contact/index.html; about/index.html",
    ),
    Topic(
        "join",
        "You can join Rithavo now to get access to Explore and to build your Rithavo Profile ahead of "
        "launch. If you already have an account, you can sign in.",
        "I can't create an account or sign you in. The Join and Sign in pages do that.",
        ("sign_up", "sign_in"),
        ("What is Explore?", "What is a Rithavo Profile?", "When does it launch?"),
        "index.html (Join Rithavo; Sign In)",
    ),
    Topic(
        "systems_growth",
        "Rithavo Systems is planned as small, practical automations for small teams, and Rithavo Growth "
        "is planned for growing a business. Neither is live yet; Career comes first.",
        "Each page has a way to tell Rithavo what you'd want built.",
        ("systems", "growth"),
        ("What is Rithavo?", "What is Career Intelligence?", "How do I contact Rithavo?"),
        "systems/index.html; growth/index.html",
    ),
    Topic(
        "contact",
        "The fastest way to reach Rithavo is email: hello@rithavo.com. That covers questions before you "
        "buy, orders and billing, and feedback. For refunds, see the Terms first.",
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
        ("What is Career Intelligence?", "What is Application Diagnosis?", "What is Rithavo?"),
        "about/index.html; footer disclaimer on career/index.html",
    ),
)}

# --- answers that are not topics -------------------------------------------------

REFUSE_PERSONAL = (
    PERSONAL_BOUNDARY + " That is what Career Intelligence and Application Diagnosis are for, and I can "
    "point you to them."
)
REFUSE_PERSONAL_ACTIONS = ("career_intelligence", "application_diagnosis")
REFUSE_PERSONAL_SUGGESTIONS = ("What is Career Intelligence?", "What is Application Diagnosis?",
                               "Which one should I choose?")

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
UNKNOWN_SUGGESTIONS = ("What is Rithavo?", "What is Career Intelligence?", "What is Application Diagnosis?",
                       "How do I contact Rithavo?")
UNKNOWN_REPEATED = (
    UNKNOWN + " If it's something specific, emailing hello@rithavo.com is the surest way to get an answer."
)

NO_TOPIC_FOR_FOLLOWUP = "Which one do you mean? I can tell you about any of these."
START_SUGGESTIONS = ("What is Rithavo?", "What is Career Intelligence?", "What is Application Diagnosis?",
                     "Which one should I choose?")

# Messages the unavailable / rate-limited states show. Kept here so the wording is reviewed with the rest.
BUSY = "I'm getting a lot of questions right now. Please try again in a minute."
UNAVAILABLE = "I can't answer right now. Your conversation is kept. The links below still work."
FALLBACK_ACTIONS = ("career", "contact", "about")


# One line the panel always shows, so nobody mistakes the assistant for live AI.
DISCLOSURE = (
    "I answer from Rithavo's approved product information. I'm not a live AI, I can't see your account, "
    "and I can't give personal career advice."
)

# Starter questions by page, so the guide is useful where the person is. Every phrase here is asked of the
# real matcher in tests/test_ritanya_chat.py and must produce a reviewed topic answer, never "I don't know".
PAGE_STARTERS = {
    "/":                                       START_SUGGESTIONS,
    "/career/":                                ("What is Career Intelligence?", "What is Application Diagnosis?", "Which one should I choose?"),
    "/career/career-intelligence/":            ("What is Career Intelligence?", "Which one should I choose?", "When does it launch?"),
    "/career/application-diagnostic/":         ("What is Application Diagnosis?", "Which one should I choose?", "When does it launch?"),
    "/career-intelligence/":                   ("What is Career Intelligence?", "What is a Rithavo Profile?", "When does it launch?"),
    "/application-diagnosis/":                 ("What is Application Diagnosis?", "What is a Rithavo Profile?", "When does it launch?"),
    "/home/":                                  ("What is Explore?", "What is Mentorship?", "What is a Rithavo Profile?"),
    "/profile/":                               ("What is a Rithavo Profile?", "What is a Rithavo Card?", "What is Career Intelligence?"),
    "/resumes/":                               ("What is Application Diagnosis?", "What is a Rithavo Profile?"),
    "/history/":                               ("What is Application Diagnosis?", "What is Career Intelligence?"),
    "/mentorship/":                            ("What is Mentorship?", "What is Explore?"),
    "/systems/":                               ("What are Systems and Growth?", "What is Rithavo?"),
    "/growth/":                                ("What are Systems and Growth?", "What is Rithavo?"),
    "/about/":                                 ("What is Rithavo?", "How does Rithavo work?", "How do I join?"),
    "/contact/":                               ("How do I contact Rithavo?", "What is Rithavo?"),
    "/privacy/":                               ("How does Rithavo handle my data?", "What can you help me with?"),
    "/terms/":                                 ("How do I contact Rithavo?", "What is Rithavo?"),
    "/account/":                               ("What can you help me with?", "How do I contact Rithavo?"),
}
