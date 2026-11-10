#!/usr/bin/env python3
"""Generate a 256-bit key for RITHAVO_CRM_RL_HMAC_KEY (base64url). Run it on YOUR machine; the value is printed once
to your terminal and nowhere else. Store it in your password manager, then enter it in Vercel as a Sensitive variable
(Preview, branch feature/crm-lite-preview). Never paste it into chat, a file in the repository, or a log."""

import base64
import os
import sys

key = base64.urlsafe_b64encode(os.urandom(32)).decode()
sys.stdout.write(key + "\n")
sys.stderr.write("Generated a 256-bit key. It was printed once above; it is not stored anywhere by this script.\n")
