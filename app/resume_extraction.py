"""
P0 onboarding — resume upload -> extraction -> review/edit -> confirm.

No PDF/DOCX text-extraction or resume-parsing infrastructure existed
anywhere in this project before this phase (checked: rithavo-web-platform,
rithavo-site, and the sibling rithavo-career-profile codebase — the
latter's own "extraction" is extract_evidence_from_profile, which reads
an ALREADY-STRUCTURED CareerProfile, not a raw file; it has no resume
upload/parsing surface to reuse). This module is new, deliberately
small, and heuristic (regex/keyword-section based, not ML) — the
product brief's own required flow (extract -> present -> let the user
edit -> only save on explicit confirm) is the safety net for exactly
that: this never has to be perfect, because nothing here is ever
persisted as canonical profile data. See main.py's onboarding routes for
the two-step split this module enables (an /onboarding/resume/extract
preview step that calls this and returns a draft, and a completely
separate /onboarding/confirm step — the only place anything actually
gets written — that a human has reviewed the draft before reaching).

Every function here is a pure function: bytes/text in, a plain dict out,
no database access, no side effects.
"""

import re
from io import BytesIO

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?)?\d{3,4}[\s.-]?\d{3,4}")
_YEAR_RANGE_RE = re.compile(
    r"(?P<start>(?:19|20)\d{2}|[A-Za-z]{3,9}\.?\s+(?:19|20)\d{2})\s*[-–—to]+\s*"
    r"(?P<end>(?:19|20)\d{2}|[A-Za-z]{3,9}\.?\s+(?:19|20)\d{2}|[Pp]resent|[Cc]urrent)"
)

_EDUCATION_HEADERS = {"education", "academic background", "qualifications"}
_EXPERIENCE_HEADERS = {
    "experience", "work experience", "employment history", "professional experience",
    "work history", "employment",
}
_SUMMARY_HEADERS = {"summary", "profile", "objective", "about", "professional summary"}
_STOP_HEADERS = _EDUCATION_HEADERS | _EXPERIENCE_HEADERS | _SUMMARY_HEADERS | {
    "skills", "certifications", "projects", "awards", "languages", "references",
    "contact", "achievements", "interests",
}


class UnsupportedResumeFormatError(Exception):
    """Raised for anything that isn't a PDF or DOCX file."""


def extract_text_from_pdf(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def extract_text_from_docx(data: bytes) -> str:
    from docx import Document
    doc = Document(BytesIO(data))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def extract_text(filename: str, data: bytes) -> str:
    lower = (filename or "").lower()
    if lower.endswith(".pdf"):
        return extract_text_from_pdf(data)
    if lower.endswith(".docx"):
        return extract_text_from_docx(data)
    raise UnsupportedResumeFormatError("Only PDF and DOCX resumes are supported.")


def _split_sections(lines: list) -> dict:
    """Splits resume lines into named sections using a line that's just a
    known header word (case-insensitive, ignoring trailing punctuation)
    as the section boundary. Everything before the first recognized
    header goes into 'header' (usually name/contact/summary)."""
    sections = {"header": []}
    current = "header"
    for line in lines:
        stripped = line.strip().strip(":").strip()
        key = stripped.lower()
        if key in _STOP_HEADERS and len(stripped) < 40:
            if key in _EDUCATION_HEADERS:
                current = "education"
            elif key in _EXPERIENCE_HEADERS:
                current = "experience"
            elif key in _SUMMARY_HEADERS:
                current = "summary"
            else:
                current = key
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return sections


def _guess_name(header_lines: list, email: str) -> str:
    for line in header_lines[:5]:
        candidate = line.strip()
        if not candidate or _EMAIL_RE.search(candidate) or _PHONE_RE.fullmatch(candidate.replace(" ", "")):
            continue
        # A plausible name line: short, mostly letters/spaces, no digits.
        if 2 <= len(candidate.split()) <= 5 and len(candidate) < 60 and not any(c.isdigit() for c in candidate):
            return candidate
    return ""


def _entry_blocks(section_lines: list) -> list:
    """Groups a section's lines into entry-blocks, anchored on each line
    that contains a recognizable date range (the most reliable per-entry
    boundary a plain-text resume reliably offers). Handles both common
    layouts: the date range sharing a line with the role/company header
    ("Role, Company — Jan 2020-Present"), and the date range on its OWN
    line immediately after a separate header line ("Role, Company" /
    "Jan 2020 - Present") — a naive split-at-the-date-line approach gets
    the second layout wrong (it strands the header line in the PREVIOUS
    entry's block), so each anchor claims the single line immediately
    before it as its own header, as long as that line isn't itself
    another entry's date anchor and hasn't already been claimed by an
    earlier entry."""
    anchor_indices = [i for i, line in enumerate(section_lines) if _YEAR_RANGE_RE.search(line)]
    if not anchor_indices:
        return [section_lines] if any(l.strip() for l in section_lines) else []

    def _header_start(idx: int, floor: int) -> int:
        prev = idx - 1
        if prev > floor and not _YEAR_RANGE_RE.search(section_lines[prev]):
            return prev
        return idx

    blocks = []
    prev_end = -1
    for k, idx in enumerate(anchor_indices):
        start = _header_start(idx, prev_end)
        if k + 1 < len(anchor_indices):
            next_idx = anchor_indices[k + 1]
            stop = _header_start(next_idx, idx)
        else:
            stop = len(section_lines)
        blocks.append(section_lines[start:stop])
        prev_end = stop - 1
    return [b for b in blocks if any(l.strip() for l in b)]


def _split_header_line(header_line: str) -> tuple:
    """Splits a "Role, Company" / "Role at Company" / "Role — Company"
    style line into (left, right), stripped of the date range itself
    (which may or may not share this line — see _entry_blocks). Returns
    (whole_line, "") if no recognized separator is present."""
    stripped = _YEAR_RANGE_RE.sub("", header_line).strip(" -–—,")
    for sep in (" at ", " - ", " – ", " — ", ","):
        if sep in stripped:
            left, _, right = stripped.partition(sep)
            return left.strip(), right.strip()
    return stripped, ""


def _non_date_lines(block: list, header_line: str) -> list:
    """Every non-empty line in the block except the header itself and
    any line that, once the date range is stripped out of it, is empty
    (i.e. a line that was ONLY a date range, not a real content line)."""
    out = []
    for l in block:
        stripped = l.strip()
        if not stripped or stripped == header_line:
            continue
        if not _YEAR_RANGE_RE.sub("", stripped).strip(" -–—,"):
            continue
        out.append(stripped)
    return out


def _parse_experience_block(block: list) -> dict:
    text = "\n".join(block)
    date_match = _YEAR_RANGE_RE.search(text)
    start_date, end_date = (date_match.group("start"), date_match.group("end")) if date_match else ("", "")
    non_empty = [l.strip() for l in block if l.strip()]
    # _entry_blocks always puts the role/company header as this block's
    # own first line, whether or not the date range happens to share it.
    header_line = non_empty[0] if non_empty else ""
    role, company = _split_header_line(header_line)
    body_lines = _non_date_lines(block, header_line)
    return {
        "company": company,
        "role": role,
        "start_date": start_date,
        "end_date": end_date,
        "responsibilities": "\n".join(body_lines),
        "achievements": "",
        "industry": "",
        "function": "",
    }


def _parse_education_block(block: list) -> dict:
    text = "\n".join(block)
    date_match = _YEAR_RANGE_RE.search(text)
    start_date, end_date = (date_match.group("start"), date_match.group("end")) if date_match else ("", "")
    non_empty = [l.strip() for l in block if l.strip()]
    header_line = non_empty[0] if non_empty else ""
    degree, institution = _split_header_line(header_line)
    return {
        "degree": degree,
        "institution": institution,
        "field": "",
        "start_date": start_date,
        "end_date": end_date,
    }


def parse_resume_text(text: str) -> dict:
    """The one heuristic entry point: raw resume text in, a draft dict
    out — {identity, background, education: [...], experience: [...]}.
    Every value here is a best-effort guess meant for a human to review
    and correct, never assumed correct on its own."""
    lines = [l for l in text.splitlines()]
    sections = _split_sections(lines)

    full_text = text
    email_match = _EMAIL_RE.search(full_text)
    email = email_match.group(0) if email_match else ""
    phone_match = _PHONE_RE.search(full_text)
    phone = phone_match.group(0).strip() if phone_match else ""

    header_lines = sections.get("header", [])
    name = _guess_name(header_lines, email)
    headline = next((l.strip() for l in header_lines if l.strip() and l.strip() != name), "")

    summary_lines = sections.get("summary", [])
    summary_text = "\n".join(l for l in summary_lines if l.strip())

    experience_entries = [_parse_experience_block(b) for b in _entry_blocks(sections.get("experience", []))]
    education_entries = [_parse_education_block(b) for b in _entry_blocks(sections.get("education", []))]

    current_role = experience_entries[0]["role"] if experience_entries else ""
    companies = [e["company"] for e in experience_entries if e["company"]]
    previous_roles = [e["role"] for e in experience_entries[1:] if e["role"]]

    return {
        "identity": {
            "name": name,
            "headline": headline or summary_text[:140],
            "location": "",
            "years_of_experience": "",
            "email": email,
            "phone": phone,
        },
        "background": {
            "current_role": current_role,
            "previous_roles": previous_roles,
            "companies": companies,
            "industries": [],
            "functions": [],
        },
        "education": education_entries,
        "experience": experience_entries,
    }
