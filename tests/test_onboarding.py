"""
P0 UX redesign — profile onboarding: resume upload -> extract (preview
only) -> confirm (the only write). See app/resume_extraction.py's
module docstring for why extraction is heuristic, not ML — this file's
job is to prove the two-step split's safety property (extraction alone
never writes anything) and that the confirm step correctly persists
whatever the client sends, not to prove the heuristic parser guesses
perfectly on every possible resume layout.
"""

from io import BytesIO

from docx import Document

from .conftest import login_via_magic_link


def _make_docx(paragraphs: list) -> bytes:
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_minimal_pdf(text: str) -> bytes:
    """Hand-built, fully valid single-page PDF with one text string in
    its content stream — offsets computed for real so pypdf parses it
    normally (not via its error-recovery path), giving genuine
    end-to-end confidence in the pypdf integration itself, not just this
    module's own code."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 612 792] /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF"
    ).encode()
    return bytes(out)


def _resume_docx_bytes():
    return _make_docx([
        "Priya Sharma",
        "Senior Product Manager",
        "priya.sharma@example.com",
        "+91 98765 43210",
        "Summary",
        "Product leader with 8 years building B2B SaaS.",
        "Experience",
        "Senior Product Manager, Acme Corp",
        "Jan 2020 - Present",
        "Led the platform team; shipped three major releases.",
        "Product Manager, Beta Inc",
        "Jun 2016 - Dec 2019",
        "Owned the onboarding funnel end to end.",
        "Education",
        "MBA, Indian Institute of Management",
        "2014 - 2016",
        "B.Tech, Computer Science, IIT Delhi",
        "2010 - 2014",
    ])


# ---- extraction is a preview only — never writes anything ----

def test_extract_requires_authentication(app_and_client):
    app, client = app_and_client
    resp = client.post("/onboarding/resume/extract", files={"resume": ("resume.docx", _resume_docx_bytes())})
    assert resp.status_code == 401


def test_extract_from_docx_returns_a_draft_without_writing_anything(app_and_client, db):
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "extract-docx@example.com")
    resp = client.post("/onboarding/resume/extract", files={
        "resume": ("resume.docx", _resume_docx_bytes(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    })
    assert resp.status_code == 200, resp.text
    draft = resp.json()
    assert draft["identity"]["name"] == "Priya Sharma"
    assert draft["identity"]["email"] == "priya.sharma@example.com"
    assert len(draft["experience"]) == 2
    assert draft["experience"][0]["company"] == "Acme Corp"
    assert len(draft["education"]) == 2
    # Phase P0.4A: extraction has always derived these (see
    # app/resume_extraction.py) - the P0.4 finding was that the review/
    # confirm flow silently discarded them, not that extraction lacked
    # them. This assertion documents that the source data was never the
    # problem.
    assert draft["background"]["companies"] == ["Acme Corp", "Beta Inc"]
    assert draft["background"]["previous_roles"] == ["Product Manager"]

    # Preview only — nothing persisted.
    assert db.get_career_profile(user_id) is None
    assert db.list_education_for_user(user_id) == []
    assert db.list_experience_for_user(user_id) == []


def test_extract_from_a_real_pdf_returns_a_draft(app_and_client, db):
    """Genuine end-to-end pypdf integration test — a real, correctly-
    structured PDF byte stream, not a mock."""
    app, client = app_and_client
    login_via_magic_link(client, app, "extract-pdf@example.com")
    resp = client.post("/onboarding/resume/extract", files={
        "resume": ("resume.pdf", _make_minimal_pdf("Jordan Lee - Data Analyst"), "application/pdf"),
    })
    assert resp.status_code == 200, resp.text
    draft = resp.json()
    assert "Jordan Lee" in draft["identity"]["name"] or "Jordan Lee" in draft["identity"]["headline"]


def test_extract_rejects_unsupported_file_types(app_and_client):
    app, client = app_and_client
    login_via_magic_link(client, app, "extract-bad-type@example.com")
    resp = client.post("/onboarding/resume/extract", files={"resume": ("resume.txt", b"just some text")})
    assert resp.status_code == 422


def test_extract_rejects_a_file_with_no_extractable_text(app_and_client):
    app, client = app_and_client
    login_via_magic_link(client, app, "extract-empty@example.com")
    empty_pdf = _make_minimal_pdf("")
    resp = client.post("/onboarding/resume/extract", files={"resume": ("resume.pdf", empty_pdf, "application/pdf")})
    assert resp.status_code == 422


def test_extract_rejects_an_oversized_upload(app_and_client):
    app, client = app_and_client
    login_via_magic_link(client, app, "extract-too-big@example.com")
    oversized = b"x" * (10 * 1024 * 1024 + 1)
    resp = client.post("/onboarding/resume/extract", files={"resume": ("resume.pdf", oversized, "application/pdf")})
    assert resp.status_code == 413


# ---- confirm is the only step that writes canonical data ----

def test_confirm_requires_authentication(app_and_client):
    app, client = app_and_client
    resp = client.post("/onboarding/confirm", json={"name": "x"})
    assert resp.status_code == 401


def test_confirm_creates_the_profile_and_education_and_experience_rows(app_and_client, db):
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "confirm-full@example.com")
    resp = client.post("/onboarding/confirm", json={
        "name": "Priya Sharma", "headline": "Senior Product Manager",
        "location": "Bengaluru", "years_of_experience": "8",
        "current_role": "Senior Product Manager", "previous_roles": ["Product Manager"],
        "companies": ["Acme Corp", "Beta Inc"], "industries": ["SaaS"], "functions": ["Product"],
        "education": [
            {"degree": "MBA", "institution": "IIM", "field": "", "start_date": "2014-06", "end_date": "2016-06"},
        ],
        "experience": [
            {"company": "Acme Corp", "role": "Senior Product Manager", "start_date": "2020-01", "end_date": "",
             "responsibilities": "Led the platform team.", "achievements": "", "industry": "SaaS", "function": "Product"},
        ],
    })
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "confirmed"}

    profile = db.get_career_profile(user_id)
    assert profile is not None
    import json
    data = json.loads(profile["profile_json"])
    assert data["identity"]["name"]["value"] == "Priya Sharma"
    assert data["background"]["companies"] == ["Acme Corp", "Beta Inc"]
    assert data["background"]["previous_roles"] == ["Product Manager"]
    assert data["background"]["industries"] == ["SaaS"]

    education = db.list_education_for_user(user_id)
    assert len(education) == 1
    assert education[0]["degree"] == "MBA"

    experience = db.list_experience_for_user(user_id)
    assert len(experience) == 1
    assert experience[0]["company"] == "Acme Corp"


def test_confirm_works_for_pure_manual_entry_with_no_education_or_experience(app_and_client, db):
    """Option B: Build Manually — no resume was ever uploaded, so
    education/experience are simply omitted; the profile must still be
    created."""
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "confirm-manual@example.com")
    resp = client.post("/onboarding/confirm", json={"name": "Alex Manual", "headline": "Engineer"})
    assert resp.status_code == 200, resp.text
    profile = db.get_career_profile(user_id)
    assert profile is not None
    assert db.list_education_for_user(user_id) == []
    assert db.list_experience_for_user(user_id) == []

    # Phase P0.4A test 5: previous_roles/companies/industries left
    # entirely unset (the "Build Manually" case never sends them at all)
    # must still save cleanly as empty lists, never an error.
    import json
    data = json.loads(profile["profile_json"])
    assert data["background"]["previous_roles"] == []
    assert data["background"]["companies"] == []
    assert data["background"]["industries"] == []


def test_confirm_persists_user_edited_values_not_the_raw_extraction(app_and_client, db):
    """Phase P0.4A tests 3 and 6: extraction is only ever a preview - the
    user editing (here, correcting/trimming) the previous_roles/companies/
    industries the extraction draft suggested must result in exactly the
    edited values being saved, never the original draft re-asserting
    itself. This is what "nothing becomes canonical until confirm" means
    in practice for these three fields specifically."""
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "confirm-edited-lists@example.com")
    extract_resp = client.post("/onboarding/resume/extract", files={
        "resume": ("resume.docx", _resume_docx_bytes(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    })
    draft = extract_resp.json()
    assert draft["background"]["companies"] == ["Acme Corp", "Beta Inc"]

    # The user reviews and edits: drops "Beta Inc", adds an industry the
    # extraction never inferred (industries are always [] from
    # extraction - see resume_extraction.py).
    resp = client.post("/onboarding/confirm", json={
        "name": draft["identity"]["name"], "headline": draft["identity"].get("headline", ""),
        "current_role": draft["background"].get("current_role", ""),
        "previous_roles": draft["background"]["previous_roles"],
        "companies": ["Acme Corp"],  # edited: Beta Inc removed by the user
        "industries": ["SaaS"],      # added by the user - never came from extraction
    })
    assert resp.status_code == 200, resp.text

    import json as _json
    data = _json.loads(db.get_career_profile(user_id)["profile_json"])
    assert data["background"]["companies"] == ["Acme Corp"]
    assert data["background"]["industries"] == ["SaaS"]


def test_confirming_twice_updates_the_same_profile_row_not_a_duplicate(app_and_client, db):
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "confirm-twice@example.com")
    client.post("/onboarding/confirm", json={"name": "First Name"})
    client.post("/onboarding/confirm", json={"name": "Second Name"})
    with db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM career_profiles WHERE user_id = ?", (user_id,)
        ).fetchone()["c"]
    assert count == 1
    import json
    data = json.loads(db.get_career_profile(user_id)["profile_json"])
    assert data["identity"]["name"]["value"] == "Second Name"


def test_confirm_never_lets_a_client_set_another_users_profile(app_and_client, db):
    """Structural check: confirm_onboarding always derives the target
    user from the session (require_user), never from any client-supplied
    field — there is no user_id parameter on this route at all."""
    import inspect
    from app import main
    params = inspect.signature(main.confirm_onboarding).parameters
    assert "user_id" not in params
    assert "session_user_id" not in params  # not client-suppliable either


# ---- the redirect that gates all of this: see also
#      test_deployment_path_prefix.py for the /api-prefix-safety angle ----

def test_full_round_trip_new_user_onboards_then_lands_on_home_next_time(app_and_client, db):
    app, client = app_and_client
    app.state.email_sender.sent.clear()

    # First sign-in: no profile yet -> onboarding.
    client.post("/auth/start", data={"email": "round-trip@example.com"})
    import re
    body = app.state.email_sender.sent[-1]["body"]
    token = re.search(r"token=(\S+)", body).group(1)
    resp = client.get(f"/auth/verify?token={token}", follow_redirects=False)
    assert resp.headers["location"].endswith("/onboarding/")

    client.post("/onboarding/confirm", json={"name": "Round Tripper", "headline": "Engineer"})

    # Second sign-in (new session): profile now exists -> home.
    client.post("/auth/logout")
    app.state.email_sender.sent.clear()
    client.post("/auth/start", data={"email": "round-trip@example.com"})
    body = app.state.email_sender.sent[-1]["body"]
    token = re.search(r"token=(\S+)", body).group(1)
    resp = client.get(f"/auth/verify?token={token}", follow_redirects=False)
    assert resp.headers["location"].endswith("/home/")
