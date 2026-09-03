"""
Structured job-description signal extraction — controlled taxonomies only.

This module replaces the Phase 1.5 "everything that isn't a stopword is a
gap" approach with the opposite architecture: nothing becomes a finding
unless it matches an explicit, documented entry in one of the taxonomies
below. A JD word that matches no taxonomy is simply ignored — neither a
strength nor a gap — which is what stops filler ("running", "proficiency",
"fast-paced", "dollar", "decisions", "architectural" as bare words) from
becoming spurious "skill gaps": those words don't appear as such in any
taxonomy entry, so they never generate a finding at all.

Every taxonomy here is a plain, readable Python literal — extending
coverage for a new skill/responsibility/domain is a one-line addition,
reviewable in a diff, never an inferred or learned mapping.
"""

import re

# ============================================================================
# A. Seniority — ordered rank, highest number = most senior. Used for both
#    "what does the JD require" and "what does the profile show".
# ============================================================================
SENIORITY_LEVELS = [
    # (rank, canonical_name, {aliases as they'd appear in text, lowercase})
    (0, "Intern", {"intern", "internship", "trainee"}),
    (1, "Junior", {"junior", "entry-level", "entry level", "associate engineer"}),
    (2, "Mid-level", {"mid-level", "mid level", "intermediate"}),
    (3, "Senior", {"senior", "sr."}),
    (4, "Staff/Lead", {"staff", "lead", "tech lead", "team lead"}),
    (5, "Principal/Manager", {"principal", "manager", "engineering manager"}),
    (6, "Director", {"director", "head of"}),
    (7, "VP", {"vp", "vice president", "svp", "evp"}),
    (8, "C-suite", {"cto", "ceo", "coo", "cfo", "chief"}),
]


def extract_seniority(text: str):
    """Returns (rank, canonical_name) for the HIGHEST seniority term found
    in text, or None if none of the controlled terms appear."""
    text_l = text.lower()
    best = None
    for rank, name, aliases in SENIORITY_LEVELS:
        for alias in aliases:
            if re.search(rf"\b{re.escape(alias)}\b", text_l):
                if best is None or rank > best[0]:
                    best = (rank, name)
                break
    return best


def seniority_from_years(years: float):
    """Deterministic fallback when no explicit seniority word is present
    in a profile's title/headline: infer a coarse band purely from years
    of experience. Documented thresholds, not a model:
      0-1y -> Junior, 2-4y -> Mid-level, 5-8y -> Senior,
      9-12y -> Staff/Lead, 12y+ -> Principal/Manager.
    This is deliberately conservative — it never infers Director+ purely
    from years, since title-level seniority (people/budget scope) is not
    reliably implied by tenure alone."""
    if years is None:
        return None
    if years <= 1:
        return (1, "Junior")
    if years <= 4:
        return (2, "Mid-level")
    if years <= 8:
        return (3, "Senior")
    if years <= 12:
        return (4, "Staff/Lead")
    return (5, "Principal/Manager")


# ============================================================================
# B. Role title -> domain bucket. Used for role/title alignment scoring and
#    for career-transition detection (profile's domain vs JD's domain).
# ============================================================================
ROLE_TITLE_DOMAINS = {
    "engineer": "Engineering", "developer": "Engineering", "programmer": "Engineering",
    "architect": "Engineering", "sre": "Engineering",
    "analyst": "Data/Analytics", "scientist": "Data/Analytics", "statistician": "Data/Analytics",
    "manager": "Management", "director": "Management", "head": "Management",
    "teacher": "Education", "professor": "Education", "instructor": "Education", "educator": "Education",
    "sales": "Sales", "account executive": "Sales",
    "designer": "Design", "ux": "Design", "ui": "Design",
    "marketer": "Marketing", "marketing": "Marketing",
    "recruiter": "HR", "hr": "HR",
    "product manager": "Product", "product owner": "Product",
    "consultant": "Consulting",
}


def extract_role_title(text: str):
    """Returns (role_noun, domain_bucket) for the first recognized role
    noun found near the start of the text, or None. Longer/multi-word
    keys are checked first so "product manager" isn't shadowed by the
    bare "manager" entry."""
    text_l = text.lower()
    for key in sorted(ROLE_TITLE_DOMAINS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(key)}\b", text_l):
            return (key, ROLE_TITLE_DOMAINS[key])
    return None


# ============================================================================
# C/D. Skill taxonomy — canonical name -> aliases (DIRECT match) and
#      related concepts (RELATED/TRANSFERABLE evidence only, never DIRECT).
#      This is the explicit semantic-equivalence taxonomy — every
#      equivalence is a literal entry here, nothing inferred at runtime.
# ============================================================================
SKILL_TAXONOMY = {
    "Python": {"aliases": {"python"}, "related": set()},
    "Django": {"aliases": {"django"}, "related": {"Flask", "FastAPI"}},
    "Flask": {"aliases": {"flask"}, "related": {"Django", "FastAPI"}},
    "FastAPI": {"aliases": {"fastapi"}, "related": {"Django", "Flask"}},
    "PostgreSQL": {"aliases": {"postgresql", "postgres"}, "related": {"SQL", "MySQL"}},
    "MySQL": {"aliases": {"mysql"}, "related": {"SQL", "PostgreSQL"}},
    "SQL": {"aliases": {"sql"}, "related": {"PostgreSQL", "MySQL"}},
    "AWS": {"aliases": {"aws", "amazon web services"}, "related": {"Cloud Infrastructure", "Azure", "GCP"}},
    "Azure": {"aliases": {"azure"}, "related": {"Cloud Infrastructure", "AWS", "GCP"}},
    "GCP": {"aliases": {"gcp", "google cloud"}, "related": {"Cloud Infrastructure", "AWS", "Azure"}},
    "Cloud Infrastructure": {"aliases": {"cloud infrastructure", "cloud computing"}, "related": {"AWS", "Azure", "GCP"}},
    "Docker": {"aliases": {"docker"}, "related": {"Containerization", "Kubernetes"}},
    "Kubernetes": {"aliases": {"kubernetes", "k8s"}, "related": {"Containerization", "Docker"}},
    "Containerization": {"aliases": {"containerization", "containers", "container orchestration"},
                          "related": {"Docker", "Kubernetes"}},
    "REST APIs": {"aliases": {"rest api", "rest apis", "restful api", "restful apis", "rest"},
                  "related": {"GraphQL"}},
    "GraphQL": {"aliases": {"graphql"}, "related": {"REST APIs"}},
    "React": {"aliases": {"react", "react.js", "reactjs"}, "related": {"Frontend Development", "TypeScript"}},
    "TypeScript": {"aliases": {"typescript"}, "related": {"JavaScript"}},
    "JavaScript": {"aliases": {"javascript", "js"}, "related": {"TypeScript"}},
    "Frontend Development": {"aliases": {"frontend", "front-end"}, "related": {"React", "JavaScript", "CSS"}},
    "CSS": {"aliases": {"css"}, "related": {"Frontend Development"}},
    "Node.js": {"aliases": {"node.js", "nodejs", "node"}, "related": {"JavaScript"}},
    "Redis": {"aliases": {"redis"}, "related": {"Caching"}},
    "Caching": {"aliases": {"caching"}, "related": {"Redis"}},
    "Kafka": {"aliases": {"kafka"}, "related": {"Event-Driven Architecture"}},
    "Event-Driven Architecture": {"aliases": {"event-driven architecture", "event-driven"}, "related": {"Kafka"}},
    "CI/CD": {"aliases": {"ci/cd", "continuous integration", "continuous deployment"}, "related": {"DevOps"}},
    "DevOps": {"aliases": {"devops"}, "related": {"CI/CD", "Kubernetes", "Docker"}},
    "Microservices": {"aliases": {"microservices", "microservice architecture"}, "related": {"REST APIs"}},
    "Excel": {"aliases": {"excel", "ms excel", "microsoft excel"}, "related": {"Data Visualization"}},
    "Data Visualization": {"aliases": {"data visualization", "dashboards", "dashboarding"}, "related": {"Excel"}},
    "Product Strategy": {"aliases": {"product strategy"}, "related": {"Roadmapping"}},
    "Roadmapping": {"aliases": {"roadmapping", "roadmap ownership", "product roadmap"}, "related": {"Product Strategy"}},
    "Stakeholder Management": {"aliases": {"stakeholder management", "managing stakeholders",
                                            "manage stakeholders"}, "related": set()},
    "A/B Testing": {"aliases": {"a/b testing", "ab testing", "split testing"}, "related": {"Experimentation"}},
    "Experimentation": {"aliases": {"experimentation", "experiment design"}, "related": {"A/B Testing"}},
    "PyTorch": {"aliases": {"pytorch"}, "related": {"TensorFlow", "Machine Learning"}},
    "TensorFlow": {"aliases": {"tensorflow"}, "related": {"PyTorch", "Machine Learning"}},
    "Machine Learning": {"aliases": {"machine learning", "ml"}, "related": {"PyTorch", "TensorFlow", "MLOps"}},
    "MLOps": {"aliases": {"mlops"}, "related": {"Machine Learning", "DevOps"}},
    "Distributed Training": {"aliases": {"distributed training", "gpu clusters", "gpu cluster"},
                              "related": {"Machine Learning"}},
    "LLM Fine-Tuning": {"aliases": {"llm fine-tuning", "fine-tuning", "llm"}, "related": {"Machine Learning"}},
    "Vector Databases": {"aliases": {"vector database", "vector databases"}, "related": {"Machine Learning"}},
    "Agile/Scrum": {"aliases": {"agile", "scrum"}, "related": set()},
    "Git": {"aliases": {"git", "version control"}, "related": set()},
}


def _find_skill_mentions(text: str) -> set:
    """Canonical skill names whose alias literally appears in text."""
    text_l = text.lower()
    found = set()
    for canonical, spec in SKILL_TAXONOMY.items():
        for alias in spec["aliases"]:
            if re.search(rf"\b{re.escape(alias)}\b", text_l):
                found.add(canonical)
                break
    return found


_PREFERRED_CUES = re.compile(
    r"\b(preferred|nice[- ]to[- ]have|a plus|bonus|desirable|is a plus)\b", re.IGNORECASE
)
_REQUIRED_CUES = re.compile(r"\b(required|must have|must|essential|requires)\b", re.IGNORECASE)


def extract_skills(jd_text: str):
    """Splits the JD into sentences; a skill mentioned in a sentence
    carrying a 'preferred' cue is classified PREFERRED, everything else
    recognized is REQUIRED by default (most JD skill lists have no
    explicit required/preferred marker at all, and the conventional
    reading of an unqualified requirements list is that it's required).
    Returns (required: set[str], preferred: set[str])."""
    sentences = re.split(r"(?<=[.!?])\s+", jd_text)
    required, preferred = set(), set()
    for sentence in sentences:
        mentions = _find_skill_mentions(sentence)
        if not mentions:
            continue
        if _PREFERRED_CUES.search(sentence):
            preferred |= mentions
        else:
            required |= mentions
    # A skill only ever mentioned in a preferred-cue sentence stays
    # preferred; if the same skill also appears in a required-cue
    # sentence elsewhere, required wins (it's a real requirement
    # somewhere in the JD).
    preferred -= required
    return required, preferred


# ============================================================================
# E. Responsibilities — multi-word phrases, matched the same way as skills.
# ============================================================================
RESPONSIBILITY_TAXONOMY = {
    "Team Leadership": {"team leadership", "leading a team", "leading teams", "leading engineering",
                         "mentoring", "managing engineers", "team lead", "tech lead"},
    "Roadmap Ownership": {"roadmap ownership", "own the roadmap", "owning the roadmap", "product roadmap"},
    "Stakeholder Management": {"stakeholder management", "managing stakeholders", "manage stakeholders",
                                "working with stakeholders"},
    "Code Review": {"code review", "code reviews", "reviewing code"},
    "Architectural Decisions": {"architectural decisions", "architecture decisions",
                                 "setting technical strategy", "technical strategy"},
    "Budget Management": {"budget management", "managing budgets", "manage a budget", "p&l responsibility",
                           "p&l ownership"},
    "Cross-Functional Collaboration": {"cross-functional", "collaborate with product", "collaborate with designers"},
    "Hiring": {"hiring", "recruiting engineers", "building the team"},
}


def extract_responsibilities(text: str) -> set:
    text_l = text.lower()
    found = set()
    for canonical, phrases in RESPONSIBILITY_TAXONOMY.items():
        for phrase in phrases:
            if phrase in text_l:
                found.add(canonical)
                break
    return found


# ============================================================================
# F. Experience-years requirement.
# ============================================================================
_YEARS_RE = re.compile(r"(\d{1,2})\+?\s*(?:years?|yrs?)", re.IGNORECASE)


def extract_years_required(text: str):
    matches = [int(m) for m in _YEARS_RE.findall(text)]
    return max(matches) if matches else None


# ============================================================================
# G. Scope — team size and budget/P&L responsibility.
# ============================================================================
_TEAM_SIZE_RE = re.compile(
    r"(\d{1,4})\+?\s*(?:people|person team|employees|direct reports|engineers|reports)", re.IGNORECASE
)
_BUDGET_CUES = re.compile(
    r"\b(multi-million[- ]dollar|multi[- ]million|budget|p&l responsibility|p&l ownership)\b", re.IGNORECASE
)


def extract_team_size_required(text: str):
    matches = [int(m) for m in _TEAM_SIZE_RE.findall(text)]
    return max(matches) if matches else None


def extract_budget_scope_required(text: str) -> bool:
    return bool(_BUDGET_CUES.search(text))


# ============================================================================
# H. Education / certification.
# ============================================================================
EDUCATION_TAXONOMY = {
    "Bachelor's Degree": {"bachelor's", "bachelors", "b.tech", "b.e.", "bsc", "b.sc"},
    "Master's Degree": {"master's", "masters", "m.tech", "msc", "m.sc"},
    "MBA": {"mba"},
    "PhD": {"phd", "doctorate"},
    "Certification": {"certified", "certification", "certificate"},
}


def extract_education_required(text: str) -> set:
    text_l = text.lower()
    found = set()
    for canonical, aliases in EDUCATION_TAXONOMY.items():
        for alias in aliases:
            if re.search(rf"\b{re.escape(alias)}\b", text_l):
                found.add(canonical)
                break
    return found


# ============================================================================
# I. Domain / industry.
# ============================================================================
DOMAIN_TAXONOMY = {
    "Fintech": {"fintech", "financial technology", "payments"},
    "B2B SaaS": {"b2b saas", "saas", "b2b"},
    # Phase 1.5 hardening fix: "retail" was originally an E-commerce alias,
    # which spuriously matched a customer whose *employer* happens to be an
    # e-commerce company against a JD asking for *retail sales* experience
    # — two different things (working in e-commerce engineering is not
    # retail sales experience). Retail Sales is now its own bucket.
    "E-commerce": {"e-commerce", "ecommerce"},
    "Retail Sales": {"retail", "retail sales", "merchandising"},
    "Healthcare": {"healthcare", "health tech", "medtech"},
    "Education": {"education", "edtech"},
    "FMCG": {"fmcg", "consumer goods"},
    "Manufacturing": {"manufacturing"},
    "Gaming": {"gaming", "games"},
}


def extract_domain(text: str) -> set:
    text_l = text.lower()
    found = set()
    for canonical, aliases in DOMAIN_TAXONOMY.items():
        for alias in aliases:
            if re.search(rf"\b{re.escape(alias)}\b", text_l):
                found.add(canonical)
                break
    return found


# ============================================================================
# J. Location / work mode — informational only, never scored (the profile
#    has no location-preference field to compare against).
# ============================================================================
_LOCATION_CUES = {
    "Remote": {"remote", "work from home", "wfh"},
    "Hybrid": {"hybrid"},
    "On-site": {"on-site", "onsite", "in-office"},
}


def extract_location_mode(text: str):
    text_l = text.lower()
    for canonical, aliases in _LOCATION_CUES.items():
        for alias in aliases:
            if alias in text_l:
                return canonical
    return None


# ============================================================================
# K. Behavioral / generic language — recognized explicitly so it can be
#    reported as "present but not evaluated", never silently turned into a
#    skill gap by falling through to a denylist-of-everything-else.
# ============================================================================
BEHAVIORAL_LANGUAGE_TERMS = {
    "fast-paced", "fast paced", "growth mindset", "team player", "excellent communication",
    "communication skills", "can-do attitude", "rockstar", "ninja", "guru", "wear many hats",
    "self-starter", "detail-oriented", "passionate", "hard-working", "go-getter", "thrives",
    "great attitude", "positive attitude", "strong work ethic", "adaptable", "proactive",
}


def extract_behavioral_terms(text: str) -> set:
    text_l = text.lower()
    return {term for term in BEHAVIORAL_LANGUAGE_TERMS if term in text_l}
