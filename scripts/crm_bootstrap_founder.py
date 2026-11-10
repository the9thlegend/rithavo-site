#!/usr/bin/env python3
"""
Owner-run issuance of a ONE-TIME Founder bootstrap token (Rithavo Nexus).

Run this on YOUR machine. It prints, to separate streams:
  stdout: SQL that contains ONLY the SHA-256 hash of the token. Paste it into the Supabase SQL editor of the
          rithavo-crm-preview project (as the owner). It aborts unless the email is a Super Admin.
  stderr: the raw token and the link, ONCE. They are not stored, logged or emailed. Open the link yourself,
          within 30 minutes, on the device you will use; it works once.
Issuing a new token revokes any open one. If the token is lost or expires, run this again.
Usage: python scripts/crm_bootstrap_founder.py --email founder.preview@example.test --base https://<preview-host>
"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.crm import authmigration, invites  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--email", required=True)
ap.add_argument("--base", required=True, help="origin of the deployment, e.g. https://rithavo-site-git-feature-crm-lite-preview-legend-db1e.vercel.app")
ap.add_argument("--ttl", type=int, default=30 * 60)
args = ap.parse_args()

token = invites.new_token()
now = int(time.time())
out = authmigration.bootstrap_sql(args.email, invites.hash_token(token), now,
                                  datetime.now(timezone.utc).isoformat(), args.ttl)
sys.stdout.write("-- Rithavo Nexus Founder bootstrap (hash only). Run as the owner in the rithavo-crm-preview project.\n")
sys.stdout.write(out["sql"])
sys.stderr.write("\nOne-time link (shown once, valid for %d minutes, works once):\n  %s/crm/#/accept?token=%s\n"
                 % (args.ttl // 60, args.base.rstrip("/"), token))
