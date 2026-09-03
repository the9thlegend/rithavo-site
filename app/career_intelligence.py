"""
Phase 2B-1.7A — Career Intelligence read-only presentation adapter.

This module contains ZERO scoring, matching, synthesis, or recommendation
logic. Every substantive field it returns (fit bands, gap descriptions,
recommendation rationale, readiness bands) is copied verbatim from a row
the sibling app's own Fit/Gap/Recommendation/Readiness engines already
computed and persisted — this service reads them, exactly the same
"already-computed, already-persisted, just SELECT it" pattern already
used for career_profiles/capabilities since Phase 0.

Two small pieces of assembly happen here, and only these two:

1. "Which row is the current one" — a plain status='ACTIVE' filter,
   the identical convention every one of these tables already uses in
   the sibling app's own db.py. Not a judgment call.

2. Classifying a target as Stay / New Role / New Industry / Major
   Transition. Phase 2B-1.7A Task 1 re-inspected this specifically —
   findings, not assumptions:

   - There is NO stored field anywhere in the schema that names this
     classification (confirmed by reading every CI table's real DDL).
   - There is no "current_role_id"/"current_industry_id" anywhere
     either — discover_role_paths/discover_industry_paths
     (app/intelligence/discovery.py) score EVERY seeded Role/Industry
     against the profile's derived capabilities; nothing marks one as
     "the current one" to diff a target against.
   - discovery.py's own docstring states the constraint directly:
     "ROLE/INDUSTRY SEPARATION: discover_role_paths and
     discover_industry_paths never combine into one score... no
     combined Role x Industry scoring" — i.e. the engine itself has no
     concept of "a single target that changes both role AND industry
     together." A combined "Major Transition" evaluation, as one fit
     computation, does not exist in the source of truth to read.
   - career_paths.target_role_id/target_industry_id are populated
     mutually-exclusively per row (confirmed from the sibling's own
     schema comments) — structurally, one path is either a role
     change or an industry change, never both.

   Given the product's four-way framework has no corresponding stored
   or computable representation, the rule below is the minimum
   deterministic presentation rule that can express it from what
   IS structural: STAY when the assessment has no stated target;
   NEW_ROLE/NEW_INDUSTRY from which single field is populated on the
   stated path; MAJOR_TRANSITION only when the stated-plus-alternative
   path set includes both a role-type and an industry-type path (the
   only way "both dimensions" can appear at all, given the engine's own
   role/industry separation). This is presentation-level labeling over
   already-computed facts, not a new scoring model — but it is this
   service's own construction, not a fact read from a table, and
   should be treated as provisional until confirmed against real
   production CI data.

Explicitly NOT reproduced (by design, not oversight): evidence-level
capability detail (needs the sibling's `evidence` table, not added —
avoiding schema growth beyond what this view needs), and "evolution
since your last assessment" (computed by
app/intelligence/evolution_engine.py — a real diff/narrative engine,
not a plain read; omitting it is a legitimate, honest scope reduction,
not an approximation of it).
"""

import json


def _target_name(db, career_path) -> dict:
    if career_path is None:
        return None
    if career_path["target_role_id"] is not None:
        role = db.get_role(career_path["target_role_id"])
        return {"type": "ROLE", "name": role["name"] if role else None}
    if career_path["target_industry_id"] is not None:
        industry = db.get_industry(career_path["target_industry_id"])
        return {"type": "INDUSTRY", "name": industry["name"] if industry else None}
    return None


def _classify_direction(primary_path, alternative_paths) -> str:
    """See the module docstring — this mapping is inferred from field
    presence, not a stored label. Confirm before treating it as final
    product copy."""
    if primary_path is None:
        return "STAY"
    has_role = primary_path["target_role_id"] is not None
    has_industry = primary_path["target_industry_id"] is not None
    other_has_role = any(p["target_role_id"] is not None for p in alternative_paths if p)
    other_has_industry = any(p["target_industry_id"] is not None for p in alternative_paths if p)
    role_present = has_role or other_has_role
    industry_present = has_industry or other_has_industry
    if role_present and industry_present:
        return "MAJOR_TRANSITION"
    if has_role:
        return "NEW_ROLE"
    if has_industry:
        return "NEW_INDUSTRY"
    return "STAY"


def _standing_for_path(db, user_id: int, career_path):
    if career_path is None:
        return None
    if career_path["target_role_id"] is not None:
        fit = db.find_active_role_fit(user_id, career_path["target_role_id"])
    elif career_path["target_industry_id"] is not None:
        fit = db.find_active_industry_fit(user_id, career_path["target_industry_id"])
    else:
        fit = None
    if fit is None:
        return None
    return {
        "current_fit_band": fit["current_fit_band"],
        "transferable_fit_band": fit["transferable_fit_band"],
        "transition_effort_band": fit["transition_effort_band"],
        "confidence": fit["confidence"],
        "reasoning": fit["reasoning"],
    }


def _gaps_and_next_actions(db, career_path):
    """Always returns (gaps, next_actions, transition) — transition is
    None (and both lists empty) whenever there's no path, or no ACTIVE
    transition for it yet."""
    if career_path is None:
        return [], [], None
    transition = db.find_active_career_transition_for_path(career_path["id"])
    if transition is None:
        return [], [], None
    gaps_out, actions_out = [], []
    for gap in db.list_active_gaps_for_transition(transition["id"]):
        gaps_out.append({
            "description": gap["description"], "gap_type": gap["gap_type"],
            "severity": gap["severity"], "priority": gap["priority"],
        })
        for rec in db.list_recommendations_for_gap(gap["id"]):
            actions_out.append({
                "description": gap["description"], "recommendation_type": rec["recommendation_type"],
                "status": rec["decision"], "rationale": rec["rationale"],
            })
    return gaps_out, actions_out, transition


def _readiness_for_transition(db, transition):
    if transition is None:
        return None
    row = db.find_active_readiness_for_transition(transition["id"])
    if row is None:
        return None
    return {
        "overall_band": row["overall_band"], "confidence": row["confidence"],
        "reasoning": row["reasoning"], "strengths": json.loads(row["strengths"] or "[]"),
        "transfers": json.loads(row["transfers"] or "[]"),
    }


CTA_FOR_CLASSIFICATION = {
    "STAY": "Improve My Resume",
    "NEW_ROLE": "Create My Resume for This Role",
    "NEW_INDUSTRY": "Create My Resume for This Industry",
    "MAJOR_TRANSITION": "Create My Resume for This Target",
}


RESUME_CONTEXT_FOR_CLASSIFICATION = {
    "STAY": "SAME_CAREER",
    "NEW_ROLE": "NEW_TARGET",
    "NEW_INDUSTRY": "NEW_TARGET",
    "MAJOR_TRANSITION": "NEW_TARGET",
}


def resume_inputs_for_view(view: dict):
    """Phase 2B-1.7A Task 2. Derives what app/resume_export.py's
    build_tailored_resume() needs to produce a CI-tailored resume,
    using ONLY fields already present in get_career_intelligence_view's
    output — no new persisted-data read, no engine call.

    - resume_context: SAME_CAREER for Stay, NEW_TARGET for every other
      classification (per the approved mapping).
    - target_label: stands in for build_tailored_resume's `jd_text`
      parameter, which it only ever uses for a short display label
      (its first line) — never parsed as a real job description. Using
      the CI target's name here is exactly that same "short label"
      role, not a repurposing of JD-parsing logic.
    - matched_keywords: the ACTIVE ReadinessAssessment's own persisted
      `strengths`/`transfers` lists (verbatim, already read by
      get_career_intelligence_view) stand in for build_tailored_resume's
      keyword-prioritization input — real, already-computed CI output,
      not a fabricated JD-keyword-match. Empty (a plain, unprioritized
      resume) when no readiness data exists yet for this target — never
      invented.
    """
    if not view.get("has_assessment"):
        return None
    classification = view["classification"]
    resume_context = RESUME_CONTEXT_FOR_CLASSIFICATION[classification]
    target = view.get("primary_target")
    target_label = target["name"] if target and target.get("name") else "your current career"
    readiness = view.get("readiness") or {}
    matched_keywords = list(readiness.get("strengths") or []) + list(readiness.get("transfers") or [])
    return {
        "resume_context": resume_context,
        "target_label": target_label,
        "matched_keywords": matched_keywords,
    }


def get_career_intelligence_view(db, user_id: int) -> dict:
    assessment = db.find_active_career_direction_assessment(user_id)
    if assessment is None:
        return {"has_assessment": False}

    primary_path = (
        db.get_career_path(assessment["stated_direction_career_path_id"])
        if assessment["stated_direction_career_path_id"] else None
    )
    alt_ids = json.loads(assessment["alternative_career_path_ids"] or "[]")
    alt_paths = [db.get_career_path(pid) for pid in alt_ids]
    alt_paths = [p for p in alt_paths if p is not None]

    classification = _classify_direction(primary_path, alt_paths)
    gaps, next_actions, transition = _gaps_and_next_actions(db, primary_path)

    return {
        "has_assessment": True,
        "classification": classification,
        "resume_cta_label": CTA_FOR_CLASSIFICATION[classification],
        "primary_target": _target_name(db, primary_path),
        "alternative_targets": [_target_name(db, p) for p in alt_paths],
        "current_standing": _standing_for_path(db, user_id, primary_path),
        "gaps": gaps,
        "next_actions": next_actions,
        "readiness": _readiness_for_transition(db, transition),
        "stated_direction_fit_summary": assessment["stated_direction_fit_summary"],
    }
