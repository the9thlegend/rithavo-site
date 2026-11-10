#!/usr/bin/env python3
"""
Guarded drop of the three CRM authentication tables (Preview only). This script only GENERATES the SQL after
runner-side pre-flight checks; it never connects to a database and never executes anything. The SQL is a single
transaction whose guards (project marker, owner role, synthetic-only data, exact approved counts, nothing newer than
the approval, no dependents, no active crm_app work) abort on any mismatch.

Always run the DRY RUN first and read its result ('DRY RUN OK ...'). Only after the Founder's separate written
approval naming this action, re-run with --real. Production is hard-refused.
"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.crm import authmigration as am  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--project-ref", required=True)
ap.add_argument("--project-name", required=True)
ap.add_argument("--target-host", required=True, help="the pooler host the application uses (no credentials)")
ap.add_argument("--target-port", default="6543")
ap.add_argument("--target-db", default="postgres")
ap.add_argument("--target-user", default="crm_app")
ap.add_argument("--credentials", type=int, required=True)
ap.add_argument("--invites", type=int, required=True)
ap.add_argument("--rate-limits", type=int, required=True)
ap.add_argument("--max-companies", type=int, required=True)
ap.add_argument("--max-opportunities", type=int, required=True)
ap.add_argument("--real", action="store_true", help="omit for the dry run")
a = ap.parse_args()

try:
    fp = am.drop_preflight((a.target_host, a.target_port, a.target_db, a.target_user),
                           project_ref=a.project_ref, project_name=a.project_name)
except ValueError as e:
    sys.stderr.write(f"ABORT before any SQL: {e}\n")
    sys.exit(2)
now = int(time.time())
sys.stderr.write(f"Pre-flight passed (project ref and name match, fingerprint {fp}). Mode: {'REAL' if a.real else 'DRY RUN'}.\n")
sys.stdout.write(am.drop_sql(expected_credentials=a.credentials, expected_invites=a.invites,
                             expected_rate_limits=a.rate_limits, max_companies=a.max_companies,
                             max_opportunities=a.max_opportunities, approved_at_epoch=now,
                             approved_at_iso=datetime.now(timezone.utc).isoformat(), dry_run=not a.real))
