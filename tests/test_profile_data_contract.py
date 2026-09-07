"""
Phase P0.2H — the career_profiles.profile_json data-contract fix.

Root cause (see the P0.2G diagnostic report): this service's
upsert_career_profile wrote profile_json shaped as
{"identity": {...}, "background": {...}} with no top-level "user_id"
key, but the sibling rithavo-career-profile app's own
CareerProfile.from_dict() (its models.py) accesses d["user_id"]
unconditionally — every other field in that contract already tolerates
a missing key via dict.get() fallbacks (verified by reading
CareerProfile.from_dict/ProvenancedField.from_dict directly), so
"user_id" was the one actual incompatibility, and it's what crashed
GET /card the first time a rithavo.com-onboarded profile was ever read
through that code path.

The fix lives entirely in upsert_career_profile (app/db.py) — this file
never touches, imports, or assumes anything about the sibling's own
models beyond the one interop test below, which genuinely proves
compatibility (in a separate Python process, against the sibling's
real CareerProfile.from_dict) rather than just asserting our own
assumption about the contract.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import login_via_magic_link

SIBLING_PATH = Path(r"C:\Local Home\Project Rithavo\rithavo-career-profile")


def _profile_json_for(db, user_id: int) -> dict:
    row = db.get_career_profile(user_id)
    assert row is not None
    return json.loads(row["profile_json"])


# =====================================================================
# B / C / D — user_id present, correct, and existing data preserved
# =====================================================================

def test_confirmed_profile_json_has_top_level_user_id_matching_the_row(app_and_client, db):
    """Test B."""
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "contract-user-id@example.com")
    resp = client.post("/onboarding/confirm", json={"name": "Contract Test", "headline": "Engineer"})
    assert resp.status_code == 200

    data = _profile_json_for(db, user_id)
    assert data["user_id"] == user_id


def test_manual_entry_profile_has_correct_user_id_and_preserves_fields(app_and_client, db):
    """Test D: manual entry (no resume) still produces a compatible,
    fully-preserved profile_json."""
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "contract-manual@example.com")
    resp = client.post("/onboarding/confirm", json={
        "name": "Manual Person", "headline": "Product Manager", "location": "Pune",
        "years_of_experience": "5", "current_role": "PM",
    })
    assert resp.status_code == 200

    data = _profile_json_for(db, user_id)
    assert data["user_id"] == user_id
    # Test C: nothing the caller passed in was discarded when user_id
    # was added.
    assert data["identity"]["name"]["value"] == "Manual Person"
    assert data["identity"]["headline"]["value"] == "Product Manager"
    assert data["identity"]["location"]["value"] == "Pune"
    assert data["identity"]["years_of_experience"]["value"] == "5"
    assert data["background"]["current_role"]["value"] == "PM"


def test_resume_extracted_style_profile_has_correct_user_id(app_and_client, db):
    """Test E: the confirm route doesn't distinguish manual entry from a
    reviewed/edited resume extraction — both converge on the same
    upsert_career_profile call with richer background fields populated,
    exactly like a real resume-derived confirm would send."""
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "contract-resume@example.com")
    resp = client.post("/onboarding/confirm", json={
        "name": "Resume Person", "headline": "Senior Engineer",
        "previous_roles": ["Engineer"], "companies": ["Acme Corp"],
        "industries": ["SaaS"], "functions": ["Engineering"],
        "education": [{"degree": "B.Tech", "institution": "IIT", "field": "CS",
                        "start_date": "2015-06", "end_date": "2019-06"}],
        "experience": [{"company": "Acme Corp", "role": "Engineer",
                         "start_date": "2019-07", "end_date": "",
                         "responsibilities": "Built things.", "achievements": "",
                         "industry": "SaaS", "function": "Engineering"}],
    })
    assert resp.status_code == 200

    data = _profile_json_for(db, user_id)
    assert data["user_id"] == user_id
    assert data["background"]["companies"] == ["Acme Corp"]
    assert db.list_education_for_user(user_id)[0]["degree"] == "B.Tech"
    assert db.list_experience_for_user(user_id)[0]["company"] == "Acme Corp"


def test_repeated_confirm_keeps_correct_user_id_on_every_update(app_and_client, db):
    """Test F: profile updates (the ON CONFLICT upsert path) stay
    compatible — user_id is correct on the second write too, not just
    the first INSERT."""
    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "contract-repeat@example.com")
    client.post("/onboarding/confirm", json={"name": "First Name"})
    client.post("/onboarding/confirm", json={"name": "Second Name"})

    data = _profile_json_for(db, user_id)
    assert data["user_id"] == user_id
    assert data["identity"]["name"]["value"] == "Second Name"


# =====================================================================
# I — ownership: the JSON's user_id can never be spoofed by caller data
# =====================================================================

def test_upsert_career_profile_ignores_any_user_id_the_caller_puts_in_profile_json(db):
    """Test I: upsert_career_profile's own user_id PARAMETER is always
    authoritative, never whatever a caller happens to put inside the
    profile_json dict itself — structural proof there is no way for a
    client-influenced payload to make the stored JSON claim a different
    identity than the row it's actually stored under."""
    real_user_id = db.get_or_create_user("contract-ownership@example.com")
    db.upsert_career_profile(real_user_id, {
        "user_id": 999999,  # an attacker-controlled/mistaken value, if it ever reached here
        "identity": {"name": {"value": "Ownership Test"}},
        "background": {},
    })
    data = _profile_json_for(db, real_user_id)
    assert data["user_id"] == real_user_id


# =====================================================================
# A / G — real interop with the sibling's own CareerProfile.from_dict()
# =====================================================================

def test_rithavo_com_profile_loads_via_the_sibling_card_engines_real_model(app_and_client, db, tmp_path):
    """Tests A and G together: not an assumption about the contract —
    genuinely imports and runs the sibling's own CareerProfile.from_dict
    in a separate Python process (its own 'app' package would otherwise
    collide with this repo's own app.* modules of the same name if
    imported in-process) against this service's real output."""
    if not SIBLING_PATH.is_dir():
        pytest.skip("sibling repo not present on this machine — interop check skipped")

    app, client = app_and_client
    user_id = login_via_magic_link(client, app, "sibling-interop@example.com")
    resp = client.post("/onboarding/confirm", json={"name": "Interop Person", "headline": "Engineer"})
    assert resp.status_code == 200
    profile_json_str = db.get_career_profile(user_id)["profile_json"]

    probe = tmp_path / "_sibling_parse_probe.py"
    probe.write_text(
        "import json, sys\n"
        "from app.models import CareerProfile\n"
        "parsed = CareerProfile.from_dict(json.loads(sys.argv[1]))\n"
        "print(json.dumps({'user_id': parsed.user_id, 'name': parsed.identity.name.value}))\n"
    )
    import os
    env = {**os.environ, "PYTHONPATH": str(SIBLING_PATH)}
    result = subprocess.run(
        [sys.executable, str(probe), profile_json_str],
        cwd=SIBLING_PATH, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"sibling CareerProfile.from_dict raised: {result.stderr}"
    parsed = json.loads(result.stdout.strip())
    assert parsed["user_id"] == user_id
    assert parsed["name"] == "Interop Person"
