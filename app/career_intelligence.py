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
   Transition. There is NO stored field anywhere in the schema that
   names this classification — it does not exist as data, only as
   product language. The rule below is inferred, not verified against
   a real persisted example, from the two facts that ARE structural:
   career_paths.target_role_id/target_industry_id are populated
   mutually-exclusively per row (confirmed from the sibling's own
   schema comments), and a CareerDirectionAssessment with no stated
   target at all is the only way "no specific new direction" can be
   represented. This is flagged explicitly in the Phase 2B-1.7A report
   as this service's own inference, not a fact read from a table — a
   human familiar with the real product should confirm or correct it.

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
