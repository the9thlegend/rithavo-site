"""
Tailored resume generation for the "Create My Resume for This Job" action.

Independent of, and structurally simpler than, the sibling app's own
resume_builder.py/docx_export.py (which operate on its full CareerProfile
dataclass) — this service only has the profile as a plain dict
(career_profiles.profile_json, read verbatim) plus its own education/
experience rows, so it assembles a resume directly from those, prioritizing
whatever the diagnosis engine found to matter for this specific JD.
Nothing here invents an employer, date, credential, or achievement that
isn't already on record — every line traces back to profile_json,
capabilities, education, or experience_entries.

The result is stored once, verbatim, as frozen JSON (resumes.content_json)
at generation time — later profile edits never retroactively change a
resume that's already been generated, which is what "immutable/versioned"
requires. Re-rendering to .docx from that frozen JSON (this module's other
half) is pure and deterministic: the exact same bytes every time.
"""

import io
from dataclasses import dataclass, field

from docx import Document


@dataclass
class ResumeSection:
    heading: str
    lines: list = field(default_factory=list)


@dataclass
class TailoredResume:
    name: str
    headline: str
    location: str
    summary: str
    target_context: str  # short label naming what this was tailored for
    sections: list  # list[ResumeSection]

    def to_dict(self) -> dict:
        return {
            "name": self.name, "headline": self.headline, "location": self.location,
            "summary": self.summary, "target_context": self.target_context,
            "sections": [{"heading": s.heading, "lines": s.lines} for s in self.sections],
        }

    @staticmethod
    def from_dict(d: dict) -> "TailoredResume":
        return TailoredResume(
            name=d["name"], headline=d["headline"], location=d["location"], summary=d["summary"],
            target_context=d["target_context"],
            sections=[ResumeSection(heading=s["heading"], lines=s["lines"]) for s in d["sections"]],
        )


def _val(field_obj):
    if isinstance(field_obj, dict):
        return str(field_obj.get("value") or "")
    return str(field_obj or "")


def build_tailored_resume(profile_json: dict, capabilities: list, education: list, experience: list,
                           jd_text: str, matched_keywords: list) -> TailoredResume:
    identity = (profile_json or {}).get("identity", {}) or {}
    background = (profile_json or {}).get("background", {}) or {}

    name = _val(identity.get("name")) or "Your Name"
    headline = _val(identity.get("headline")) or _val(background.get("current_role"))
    location = _val(identity.get("location"))

    # Phase 1.5 hardening: matched_keywords are now canonical, Title-Case
    # taxonomy names (e.g. "REST APIs"), not lowercase raw JD tokens —
    # lowercase both sides of the comparison below rather than assuming a
    # casing convention.
    matched_set = {str(k).lower() for k in matched_keywords}
    # Evidence-backed capabilities that are actually relevant to this JD
    # are surfaced first — genuinely tailoring the resume to the role,
    # never adding a capability that isn't already recorded.
    relevant_caps = [c["name"] for c in capabilities if any(kw in str(c["name"]).lower() for kw in matched_set)]
    other_caps = [c["name"] for c in capabilities if c["name"] not in relevant_caps]
    ordered_caps = relevant_caps + other_caps

    summary_parts = []
    if headline:
        summary_parts.append(headline)
    years = _val(identity.get("years_of_experience"))
    if years:
        summary_parts.append(f"{years} years of experience")
    if relevant_caps:
        summary_parts.append("with demonstrated strength in " + ", ".join(relevant_caps[:3]))
    summary = ". ".join(summary_parts) + "." if summary_parts else ""

    sections = []
    if ordered_caps:
        sections.append(ResumeSection("Key Capabilities", list(ordered_caps)))

    experience_lines = []
    structured_companies = set()
    for exp in experience:
        line = f"{exp['role'] or 'Role'} — {exp['company'] or 'Company'}"
        if exp["start_date"] or exp["end_date"]:
            line += f" ({exp['start_date'] or '?'} to {exp['end_date'] or 'present'})"
        experience_lines.append(line)
        if exp["achievements"]:
            experience_lines.append(f"  Achievements: {exp['achievements']}")
        elif exp["responsibilities"]:
            experience_lines.append(f"  {exp['responsibilities']}")
        if exp["company"]:
            structured_companies.add(str(exp["company"]).strip().lower())
    # Phase 1.5 fix: previous_roles (free-text, from the older profile
    # model) and experience_entries (structured, this service's own
    # table) can legitimately describe the SAME job — a profile filled in
    # before structured entries existed, then later given a structured
    # entry too. Without this check, that one job appeared twice in the
    # generated resume, which read as a careless duplication error to
    # anyone reviewing it, not as two different jobs.
    for role in background.get("previous_roles") or []:
        role_text = str(role)
        if any(company and company in role_text.lower() for company in structured_companies):
            continue
        experience_lines.append(role_text)
    if experience_lines:
        sections.append(ResumeSection("Experience", experience_lines))

    education_lines = []
    for edu in education:
        line = f"{edu['degree'] or 'Degree'}"
        if edu["field"]:
            line += f" in {edu['field']}"
        if edu["institution"]:
            line += f" — {edu['institution']}"
        education_lines.append(line)
    if education_lines:
        sections.append(ResumeSection("Education", education_lines))

    target_context = (jd_text or "").strip().splitlines()[0][:120] if jd_text else ""

    return TailoredResume(
        name=name, headline=headline, location=location, summary=summary,
        target_context=target_context, sections=sections,
    )


def render_tailored_resume_docx(resume: TailoredResume) -> bytes:
    doc = Document()
    doc.add_heading(resume.name, level=1)
    if resume.headline:
        doc.add_paragraph(resume.headline)
    if resume.location:
        doc.add_paragraph(resume.location)
    if resume.summary:
        doc.add_heading("Summary", level=2)
        doc.add_paragraph(resume.summary)
    for section in resume.sections:
        if not section.lines:
            continue
        doc.add_heading(section.heading, level=2)
        for line in section.lines:
            doc.add_paragraph(line, style="List Bullet")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
