"""
Application Diagnosis engine — deterministic, structured, evidence-grounded.

Phase 1.5 hardening rewrite. The original version treated every JD word
that wasn't in a stopword list as a candidate "skill gap" — a denylist
architecture that let filler ("running", "proficiency", "decisions",
"architectural") through as spurious findings no matter how big the
stopword list got. This version inverts that: nothing becomes a finding
unless it matches an explicit entry in a controlled taxonomy
(app/jd_signals.py) for one of eleven structured categories (role,
seniority, required/preferred skills, responsibilities, experience-years,
scope, education, domain, location, behavioral language). A JD word that
matches nothing is simply ignored — neither a strength nor a gap.

Still no external LLM call: every taxonomy, extraction rule, and scoring
weight below is a literal, documented Python value — reviewable in a
diff, not learned or inferred at runtime.
"""

from dataclasses import dataclass, field

from app.jd_signals import (
    BEHAVIORAL_LANGUAGE_TERMS,
    EDUCATION_TAXONOMY,
    SKILL_TAXONOMY,
    extract_behavioral_terms,
    extract_budget_scope_required,
    extract_domain,
    extract_education_required,
    extract_location_mode,
    extract_responsibilities,
    extract_role_title,
    extract_seniority,
    extract_skills,
    extract_team_size_required,
    extract_years_required,
    seniority_from_years,
)

_MIN_JD_WORDS = 8
_MIN_SIGNAL_ITEMS = 2  # below this, the JD is treated as having insufficient signal


class InvalidJobDescriptionError(ValueError):
    pass


# ---- Verdict bands and their CTAs — the CTA is a function of the verdict,
# never of the raw score alone, so a major mismatch can never accidentally
# inherit "apply with confidence" language. ----

VERDICT_STRONG_MATCH = "STRONG_MATCH"
VERDICT_GOOD_MATCH = "GOOD_MATCH_ADDRESSABLE_GAPS"
VERDICT_PARTIAL_STRETCH = "PARTIAL_STRETCH"
VERDICT_LOW_MATCH = "LOW_MATCH"
VERDICT_MAJOR_MISMATCH = "MAJOR_MISMATCH"
VERDICT_INSUFFICIENT_SIGNAL = "INSUFFICIENT_JD_SIGNAL"

_CTA_BY_VERDICT = {
    VERDICT_STRONG_MATCH: (
        "Apply with confidence — your profile provides strong, direct evidence for this role's core requirements."
    ),
    VERDICT_GOOD_MATCH: (
        "A strong foundation with a few addressable gaps — consider strengthening the gaps below before "
        "applying, but this is a solid opportunity to pursue."
    ),
    VERDICT_PARTIAL_STRETCH: (
        "This is a stretch opportunity — you have some relevant evidence, but meaningful gaps remain. "
        "Consider applying if you're comfortable positioning this as a growth move, or focus on closing "
        "the biggest gaps first."
    ),
    VERDICT_LOW_MATCH: (
        "Significant gaps exist between this role and your current profile. Treat this as a long-shot "
        "application, or use the gaps below to guide what to build toward next."
    ),
    VERDICT_MAJOR_MISMATCH: (
        "This role's requirements are substantially different from your current profile — most likely due "
        "to a scope, seniority, or domain gap rather than a few missing skills. Consider whether this is "
        "the right opportunity to pursue right now, or use it to understand what a realistic next step "
        "toward it would look like."
    ),
    VERDICT_INSUFFICIENT_SIGNAL: (
        "This job description doesn't contain enough concrete, specific information (skills, "
        "responsibilities, experience requirements) for a reliable fit assessment. Consider requesting a "
        "fuller job description before deciding whether to apply."
    ),
}


@dataclass
class Finding:
    dimension: str
    finding_text: str
    evidence_excerpt: str = ""
    classification: str = ""
    match_type: str = ""  # 'DIRECT' | 'RELATED' | 'GAP' | '' (structural findings)

    def to_dict(self) -> dict:
        return {
            "dimension": self.dimension, "finding_text": self.finding_text,
            "evidence_excerpt": self.evidence_excerpt, "classification": self.classification,
            "match_type": self.match_type,
        }


@dataclass
class DiagnosisReport:
    overall_score: object  # int or None (None = insufficient signal)
    verdict: str
    cta: str
    insufficient_signal: bool = False
    category_scores: dict = field(default_factory=dict)
    direct_matches: list = field(default_factory=list)
    related_matches: list = field(default_factory=list)
    gaps: list = field(default_factory=list)
    responsibility_findings: list = field(default_factory=list)
    seniority_scope_finding: object = None  # Finding or None
    education_domain_findings: list = field(default_factory=list)
    career_transition_notice: str = ""
    behavioral_language_note: str = ""
    narrative_summary: str = ""
    next_steps: list = field(default_factory=list)

    # kept for backward-compat call sites that just want "what matched"
    @property
    def matched_keywords(self) -> list:
        return [f.classification for f in self.direct_matches + self.related_matches]

    @property
    def missing_keywords(self) -> list:
        return [f.classification for f in self.gaps]

    def all_findings(self) -> list:
        findings = list(self.direct_matches) + list(self.related_matches) + list(self.gaps)
        findings += list(self.responsibility_findings)
        findings += list(self.education_domain_findings)
        if self.seniority_scope_finding:
            findings.append(self.seniority_scope_finding)
        if self.career_transition_notice:
            findings.append(Finding(dimension="CAREER_TRANSITION_NOTICE", finding_text=self.career_transition_notice))
        return findings


def validate_jd_text(jd_text: str) -> str:
    text = (jd_text or "").strip()
    if not text:
        raise InvalidJobDescriptionError("A job description is required.")
    word_count = len(text.split())
    if word_count < _MIN_JD_WORDS:
        raise InvalidJobDescriptionError(
            f"This doesn't look like a complete job description ({word_count} words) — "
            f"please paste the full text (at least {_MIN_JD_WORDS} words)."
        )
    return text


def _val(field_obj):
    if isinstance(field_obj, dict):
        return str(field_obj.get("value") or "")
    return str(field_obj or "")


def _build_profile_corpus(profile_json: dict, capabilities: list, education: list, experience: list) -> str:
    parts = []
    identity = (profile_json or {}).get("identity", {}) or {}
    background = (profile_json or {}).get("background", {}) or {}
    parts.append(_val(identity.get("headline")))
    parts.append(_val(background.get("current_role")))
    parts.extend(str(x) for x in (background.get("previous_roles") or []))
    parts.extend(str(x) for x in (background.get("companies") or []))
    parts.extend(str(x) for x in (background.get("industries") or []))
    parts.extend(str(x) for x in (background.get("functions") or []))
    for cap in capabilities:
        parts.append(str(cap["name"]))
    for edu in education:
        parts.append(str(edu["degree"]))
        parts.append(str(edu["institution"]))
        parts.append(str(edu["field"]))
    for exp in experience:
        parts.append(str(exp["company"])); parts.append(str(exp["role"]))
        parts.append(str(exp["responsibilities"])); parts.append(str(exp["achievements"]))
        parts.append(str(exp["industry"])); parts.append(str(exp["function"]))
    return " | ".join(p for p in parts if p).lower()


def _find_excerpt_for_alias(alias: str, capabilities: list, education: list, experience: list,
                             profile_json: dict) -> str:
    """Same full-surface search as Phase 1.5's fix, keyed on a taxonomy
    alias rather than a raw JD token."""
    for cap in capabilities:
        if alias in str(cap["name"]).lower():
            return f"Capability on file: \"{cap['name']}\""
    for exp in experience:
        for field_name in ("role", "responsibilities", "achievements"):
            val = str(exp[field_name])
            if alias in val.lower():
                return f"From your experience at {exp['company'] or 'a previous role'}: \"{val[:160]}\""
        for field_name in ("industry", "function"):
            val = str(exp[field_name])
            if alias in val.lower():
                return f"From your experience at {exp['company'] or 'a previous role'}: {field_name} = \"{val}\""
    for edu in education:
        for field_name in ("degree", "institution", "field"):
            val = str(edu[field_name])
            if alias in val.lower():
                return f"From your education: \"{val}\""
    identity = (profile_json or {}).get("identity", {}) or {}
    background = (profile_json or {}).get("background", {}) or {}
    headline = _val(identity.get("headline"))
    if alias in headline.lower():
        return f"From your profile headline: \"{headline}\""
    current_role = _val(background.get("current_role"))
    if alias in current_role.lower():
        return f"From your current role: \"{current_role}\""
    for role in background.get("previous_roles") or []:
        if alias in str(role).lower():
            return f"From your profile: \"{role}\""
    for field_name in ("companies", "industries", "functions"):
        for item in background.get(field_name) or []:
            if alias in str(item).lower():
                return f"From your profile ({field_name}): \"{item}\""
    return ""


def _match_skill(canonical: str, corpus: str, capabilities: list, education: list, experience: list,
                  profile_json: dict):
    """Returns (match_type, excerpt). DIRECT if an alias of `canonical`
    itself is present in the profile's evidence; RELATED if not, but an
    alias of one of its explicitly-taxonomy-linked related concepts is —
    a related match is never returned as if it were direct evidence."""
    spec = SKILL_TAXONOMY[canonical]
    for alias in spec["aliases"]:
        if alias in corpus:
            return "DIRECT", _find_excerpt_for_alias(alias, capabilities, education, experience, profile_json)
    for related_canonical in spec["related"]:
        related_spec = SKILL_TAXONOMY.get(related_canonical, {"aliases": {related_canonical.lower()}})
        for alias in related_spec["aliases"]:
            if alias in corpus:
                excerpt = _find_excerpt_for_alias(alias, capabilities, education, experience, profile_json)
                return "RELATED", excerpt
    return "GAP", ""


def _match_responsibility(canonical: str, phrases: set, corpus: str):
    for phrase in phrases:
        if phrase in corpus:
            return True, f"Found in your profile: \"...{phrase}...\""
    return False, ""


def _profile_years(profile_json: dict):
    identity = (profile_json or {}).get("identity", {}) or {}
    raw = _val(identity.get("years_of_experience"))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _profile_seniority(profile_json: dict, profile_years):
    identity = (profile_json or {}).get("identity", {}) or {}
    background = (profile_json or {}).get("background", {}) or {}
    text = f"{_val(identity.get('headline'))} {_val(background.get('current_role'))}"
    explicit = extract_seniority(text)
    return explicit or seniority_from_years(profile_years)


def _profile_role(profile_json: dict, experience: list):
    background = (profile_json or {}).get("background", {}) or {}
    role = extract_role_title(_val(background.get("current_role")))
    if role:
        return role
    if experience:
        joined = " ".join(str(e["role"]) for e in experience)
        role = extract_role_title(joined)
        if role:
            return role
    return None


def _profile_education_terms(education: list) -> set:
    joined = " ".join(str(e["degree"]) for e in education)
    return extract_education_required(joined)


def _profile_domain_terms(profile_json: dict, experience: list) -> set:
    background = (profile_json or {}).get("background", {}) or {}
    parts = list(background.get("industries") or []) + [str(e["industry"]) for e in experience]
    return extract_domain(" ".join(parts))


def _insufficient_signal_report(jd_text: str, behavioral_terms: set) -> DiagnosisReport:
    note = ""
    if behavioral_terms:
        note = (
            "This JD mostly contains generic/behavioral language (" + ", ".join(sorted(behavioral_terms)) +
            ") rather than concrete requirements, so it wasn't converted into skill gaps."
        )
    return DiagnosisReport(
        overall_score=None, verdict=VERDICT_INSUFFICIENT_SIGNAL, cta=_CTA_BY_VERDICT[VERDICT_INSUFFICIENT_SIGNAL],
        insufficient_signal=True, behavioral_language_note=note,
        narrative_summary=(
            "This job description doesn't contain enough specific, extractable information — concrete "
            "required skills, responsibilities, an experience requirement, or seniority level — to produce "
            "a reliable fit score. " + note
        ).strip(),
        next_steps=["Consider requesting a fuller job description, or evaluate this opportunity based on "
                    "the role title and company alone rather than a fit score."],
    )


def evaluate(profile_json: dict, capabilities: list, education: list, experience: list, jd_text: str) -> DiagnosisReport:
    jd_text = validate_jd_text(jd_text)

    role_title = extract_role_title(jd_text)
    seniority_required = extract_seniority(jd_text)
    required_skills, preferred_skills = extract_skills(jd_text)
    responsibilities_required = extract_responsibilities(jd_text)
    years_required = extract_years_required(jd_text)
    team_size_required = extract_team_size_required(jd_text)
    budget_scope_required = extract_budget_scope_required(jd_text)
    education_required = extract_education_required(jd_text)
    domain_required = extract_domain(jd_text)
    behavioral_terms = extract_behavioral_terms(jd_text)

    total_signal_items = (
        len(required_skills) + len(preferred_skills) + len(responsibilities_required)
        + len(education_required) + len(domain_required)
        + (1 if seniority_required else 0) + (1 if years_required else 0)
        + (1 if (team_size_required or budget_scope_required) else 0)
        + (1 if role_title else 0)
    )
    if total_signal_items < _MIN_SIGNAL_ITEMS:
        return _insufficient_signal_report(jd_text, behavioral_terms)

    corpus = _build_profile_corpus(profile_json, capabilities, education, experience)
    profile_years = _profile_years(profile_json)
    profile_role = _profile_role(profile_json, experience)
    profile_education_set = _profile_education_terms(education)
    profile_domain_set = _profile_domain_terms(profile_json, experience)

    # ---- Career-transition detection happens BEFORE seniority scoring on
    # purpose: total years of experience in a DIFFERENT field is not a
    # reliable seniority signal for the target field a transitioner is
    # moving toward (8 years of teaching doesn't make someone "Senior" as
    # a Data Analyst). When a transition is detected, only an EXPLICIT
    # seniority word in the profile's own headline/title counts — the
    # years-based fallback inference is suppressed. ----
    career_transition_notice = ""
    if profile_role and role_title and profile_role[1] != role_title[1]:
        career_transition_notice = (
            f"Career-transition note: your primary recorded professional experience is in "
            f"{profile_role[1]} ({profile_role[0].title()}), not {role_title[1]} ({role_title[0].title()}). "
            f"The matches in this report come from capabilities, education, or self-reported skills rather "
            f"than professional work experience in this field — read this as a measure of foundational "
            f"readiness for a transition, not as proven, on-the-job professional fit in {role_title[1]}."
        )
    identity = (profile_json or {}).get("identity", {}) or {}
    background = (profile_json or {}).get("background", {}) or {}
    explicit_profile_seniority = extract_seniority(f"{_val(identity.get('headline'))} {_val(background.get('current_role'))}")
    if career_transition_notice:
        profile_seniority = explicit_profile_seniority  # years-based fallback suppressed during a transition
    else:
        profile_seniority = explicit_profile_seniority or seniority_from_years(profile_years)

    # ---- Skills: DIRECT / RELATED / GAP ----
    def _classify_skill_set(skill_set, dim_prefix):
        direct, related, gap = [], [], []
        for skill in sorted(skill_set):
            match_type, excerpt = _match_skill(skill, corpus, capabilities, education, experience, profile_json)
            if match_type == "DIRECT":
                direct.append(Finding(
                    dimension=f"{dim_prefix}_DIRECT",
                    finding_text=f"This role requires \"{skill}\", and your profile shows direct evidence of it.",
                    evidence_excerpt=excerpt, classification=skill, match_type="DIRECT",
                ))
            elif match_type == "RELATED":
                related.append(Finding(
                    dimension=f"{dim_prefix}_RELATED",
                    finding_text=(
                        f"This role requires \"{skill}\" — your profile doesn't show direct hands-on "
                        f"experience with it, but shows related/transferable evidence. This is not the "
                        f"same as direct experience."
                    ),
                    evidence_excerpt=excerpt, classification=skill, match_type="RELATED",
                ))
            else:
                gap.append(Finding(
                    dimension=f"{dim_prefix}_GAP",
                    finding_text=(
                        f"This role requires \"{skill}\", which isn't reflected — directly or through a "
                        f"related concept — anywhere in your Rithavo profile."
                    ),
                    classification=skill, match_type="GAP",
                ))
        return direct, related, gap

    req_direct, req_related, req_gap = _classify_skill_set(required_skills, "REQUIRED_SKILL")
    pref_direct, pref_related, pref_gap = _classify_skill_set(preferred_skills, "PREFERRED_SKILL")
    direct_matches = req_direct + pref_direct
    related_matches = req_related + pref_related
    gaps = req_gap + pref_gap

    # ---- Responsibilities: DIRECT only (no taxonomy of "related" responsibilities) ----
    from app.jd_signals import RESPONSIBILITY_TAXONOMY
    responsibility_findings = []
    resp_direct_count = 0
    for resp in sorted(responsibilities_required):
        matched, excerpt = _match_responsibility(resp, RESPONSIBILITY_TAXONOMY[resp], corpus)
        if matched:
            resp_direct_count += 1
            responsibility_findings.append(Finding(
                dimension="RESPONSIBILITY_DIRECT",
                finding_text=f"This role involves \"{resp}\", and your profile shows evidence of this.",
                evidence_excerpt=excerpt, classification=resp, match_type="DIRECT",
            ))
        else:
            responsibility_findings.append(Finding(
                dimension="RESPONSIBILITY_GAP",
                finding_text=f"This role involves \"{resp}\", which isn't reflected in your profile.",
                classification=resp, match_type="GAP",
            ))

    # ---- Seniority / scope: one structured finding, not a keyword list ----
    seniority_scope_finding = None
    rank_gap = 0
    if seniority_required:
        # An unknown profile seniority (explicit-only during a detected
        # transition, and no explicit word found) is treated as rank 0 —
        # conservative, not silently skipped — rather than letting an
        # unresolved comparison default to "no gap at all".
        effective_profile_rank = profile_seniority[0] if profile_seniority else 0
        rank_gap = max(0, seniority_required[0] - effective_profile_rank)
    scope_unmet = bool(team_size_required or budget_scope_required)
    seniority_major = rank_gap >= 4
    years_major = (
        years_required is not None and (
            (profile_years is not None and years_required - profile_years >= 8)
            or (profile_years is None and years_required >= 8)
        )
    )
    if seniority_required or years_required or team_size_required or budget_scope_required:
        req_parts = []
        if seniority_required:
            req_parts.append(f"{seniority_required[1]}-level seniority")
        if years_required:
            req_parts.append(f"{years_required}+ years of experience")
        if team_size_required:
            req_parts.append(f"managing {team_size_required}+ people")
        if budget_scope_required:
            req_parts.append("budget/P&L ownership")
        have_parts = []
        if profile_seniority:
            have_parts.append(f"{profile_seniority[1]}-level")
        elif career_transition_notice:
            have_parts.append("no established seniority in this target field (career transition)")
        have_parts.append(
            f"{profile_years:g} years of experience" if profile_years is not None else "no recorded years of experience"
        )
        have_parts.append("no management or budget scope recorded on your profile")
        major = seniority_major or years_major or (scope_unmet and rank_gap >= 2)
        text = f"This role requires {', '.join(req_parts)}. Your profile shows {', '.join(have_parts)}."
        if major:
            text += (
                " This is a major seniority/scope mismatch — closing this gap would require years of "
                "career progression, not a resume or profile update."
            )
        seniority_scope_finding = Finding(
            dimension="SENIORITY_SCOPE", finding_text=text,
            classification="seniority_scope", match_type="MAJOR_GAP" if major else "PARTIAL",
        )
    else:
        major = False

    # ---- Education / domain ----
    education_domain_findings = []
    if education_required:
        matched_edu = education_required & profile_education_set
        if matched_edu:
            education_domain_findings.append(Finding(
                dimension="EDUCATION_DIRECT",
                finding_text=f"This role requires {', '.join(sorted(matched_edu))}, which matches your recorded education.",
                classification=", ".join(sorted(matched_edu)), match_type="DIRECT",
            ))
        else:
            education_domain_findings.append(Finding(
                dimension="EDUCATION_GAP",
                finding_text=f"This role requires {', '.join(sorted(education_required))}, which isn't reflected in your recorded education.",
                classification=", ".join(sorted(education_required)), match_type="GAP",
            ))
    if domain_required:
        matched_dom = domain_required & profile_domain_set
        if matched_dom:
            education_domain_findings.append(Finding(
                dimension="DOMAIN_DIRECT",
                finding_text=f"This role is in {', '.join(sorted(matched_dom))}, matching your recorded industry background.",
                classification=", ".join(sorted(matched_dom)), match_type="DIRECT",
            ))
        else:
            education_domain_findings.append(Finding(
                dimension="DOMAIN_GAP",
                finding_text=f"This role is in {', '.join(sorted(domain_required))}, which doesn't match your recorded industry background.",
                classification=", ".join(sorted(domain_required)), match_type="GAP",
            ))

    # ---- Scoring: documented weights, category excluded (and its weight
    # redistributed) if the JD carries no signal for it at all. ----
    CATEGORY_WEIGHTS = {
        "required_skills": 0.40,       # hard requirements are the primary gate for most JDs
        "responsibilities": 0.15,      # shows the work was actually DONE, not just tool familiarity
        "role_title": 0.10,            # coarse but real signal of relevant career trajectory
        "seniority_scope": 0.15,       # a large gap makes a role practically unattainable regardless of skills
        "experience_years": 0.10,      # explicit years requirements are common, concrete gates
        "preferred_skills": 0.05,      # nice-to-haves matter less by definition
        "education_domain": 0.05,      # usually a soft gate, rarely disqualifying alone
    }
    category_scores = {}
    if required_skills:
        n = len(required_skills)
        credit = sum(1.0 for _ in req_direct) + sum(0.5 for _ in req_related)
        category_scores["required_skills"] = round(100 * credit / n)
    if preferred_skills:
        n = len(preferred_skills)
        credit = sum(1.0 for _ in pref_direct) + sum(0.5 for _ in pref_related)
        category_scores["preferred_skills"] = round(100 * credit / n)
    if responsibilities_required:
        category_scores["responsibilities"] = round(100 * resp_direct_count / len(responsibilities_required))
    if role_title:
        if profile_role and profile_role[0] == role_title[0]:
            category_scores["role_title"] = 100
        elif profile_role and profile_role[1] == role_title[1]:
            category_scores["role_title"] = 50
        else:
            category_scores["role_title"] = 0
    if seniority_required or team_size_required or budget_scope_required:
        s = 100 - 20 * rank_gap
        if scope_unmet:
            s = min(s, 20)
        category_scores["seniority_scope"] = max(0, s)
    if years_required:
        if profile_years is None:
            category_scores["experience_years"] = 0
        elif profile_years >= years_required:
            category_scores["experience_years"] = 100
        else:
            category_scores["experience_years"] = max(0, round(100 - 15 * (years_required - profile_years)))
    edu_dom_parts = []
    if education_required:
        edu_dom_parts.append(100 if (education_required & profile_education_set) else 0)
    if domain_required:
        edu_dom_parts.append(100 if (domain_required & profile_domain_set) else 30)
    if edu_dom_parts:
        category_scores["education_domain"] = round(sum(edu_dom_parts) / len(edu_dom_parts))

    total_weight = sum(CATEGORY_WEIGHTS[c] for c in category_scores)
    overall_score = (
        round(sum(category_scores[c] * CATEGORY_WEIGHTS[c] for c in category_scores) / total_weight)
        if total_weight else None
    )

    # ---- Verdict: seniority/scope override takes precedence over the score ----
    if overall_score is None:
        verdict = VERDICT_INSUFFICIENT_SIGNAL
    elif major:
        verdict = VERDICT_MAJOR_MISMATCH
    elif overall_score >= 75:
        verdict = VERDICT_STRONG_MATCH
    elif overall_score >= 55:
        verdict = VERDICT_GOOD_MATCH
    elif overall_score >= 30:
        verdict = VERDICT_PARTIAL_STRETCH
    elif overall_score >= 10:
        verdict = VERDICT_LOW_MATCH
    else:
        verdict = VERDICT_MAJOR_MISMATCH
    cta = _CTA_BY_VERDICT[verdict]

    narrative_parts = [
        f"Weighted fit score: {overall_score}% across {len(category_scores)} scored categories "
        f"({', '.join(category_scores)})." if overall_score is not None else "",
    ]
    if career_transition_notice:
        narrative_parts.append(career_transition_notice)
    narrative_summary = " ".join(p for p in narrative_parts if p)

    next_steps = []
    if gaps:
        next_steps.append(
            "Review the required/preferred skill gaps above — add any genuinely relevant experience or "
            "skills to your profile that you have but haven't recorded yet."
        )
    if verdict not in (VERDICT_MAJOR_MISMATCH, VERDICT_INSUFFICIENT_SIGNAL):
        next_steps.append("Use \"Create My Resume for This Job\" to generate a resume tailored to this JD.")
    if direct_matches:
        next_steps.append("Lead with the directly-evidenced strengths above when positioning yourself for this role.")

    return DiagnosisReport(
        overall_score=overall_score, verdict=verdict, cta=cta, insufficient_signal=False,
        category_scores={c: {"score": s, "weight": CATEGORY_WEIGHTS[c]} for c, s in category_scores.items()},
        direct_matches=direct_matches, related_matches=related_matches, gaps=gaps,
        responsibility_findings=responsibility_findings, seniority_scope_finding=seniority_scope_finding,
        education_domain_findings=education_domain_findings, career_transition_notice=career_transition_notice,
        narrative_summary=narrative_summary, next_steps=next_steps,
    )


def verdict_for_score(overall_score, seniority_scope_major: bool = False) -> str:
    """Reconstructs the verdict from a PERSISTED diagnosis's stored score
    (diagnostics.overall_score has no dedicated verdict column — see
    main.py's read route, which sets `seniority_scope_major` by checking
    whether a persisted SENIORITY_SCOPE finding's text flags a major
    mismatch, exactly reproducing the override evaluate() applies live)."""
    if overall_score is None:
        return VERDICT_INSUFFICIENT_SIGNAL
    if seniority_scope_major:
        return VERDICT_MAJOR_MISMATCH
    if overall_score >= 75:
        return VERDICT_STRONG_MATCH
    if overall_score >= 55:
        return VERDICT_GOOD_MATCH
    if overall_score >= 30:
        return VERDICT_PARTIAL_STRETCH
    if overall_score >= 10:
        return VERDICT_LOW_MATCH
    return VERDICT_MAJOR_MISMATCH


def cta_for_verdict(verdict: str) -> str:
    return _CTA_BY_VERDICT.get(verdict, _CTA_BY_VERDICT[VERDICT_INSUFFICIENT_SIGNAL])
