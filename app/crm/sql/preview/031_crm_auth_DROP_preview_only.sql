-- DO NOT RUN without the Founder's separate written approval. Unfilled parameters make it REFUSE.
-- Use scripts/crm_drop_preview_tables.py to generate the filled dry-run and real versions.
DO $drop$
DECLARE
  exp_cred bigint := -1; exp_inv bigint := -1;
  exp_rl bigint := -1; max_co bigint := -1; max_op bigint := -1;
  approved_epoch bigint := 0; approved_iso text := '';
  expected text := 'rithavo-crm-preview:sbejvcosjgtwchsafrng'; dbc text; rcur text; n bigint; c1 bigint; c2 bigint; c3 bigint;
BEGIN
  -- 0. approval parameters must be filled (the defaults refuse)
  IF exp_cred < 0 OR exp_inv < 0 OR exp_rl < 0 OR max_co < 0 OR max_op < 0 OR approved_epoch <= 0 OR approved_iso = '' THEN
    RAISE EXCEPTION 'drop refused: approval parameters are not filled';
  END IF;
  -- 2. database identity: owner role, crm_app present, exact identity marker (database or role comment)
  IF current_user <> 'postgres' THEN RAISE EXCEPTION 'drop refused: not connected as the owner'; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'crm_app') THEN RAISE EXCEPTION 'drop refused: role crm_app not present'; END IF;
  dbc := shobj_description((SELECT oid FROM pg_database WHERE datname = current_database()), 'pg_database');
  rcur := shobj_description((SELECT oid FROM pg_roles WHERE rolname = 'crm_app'), 'pg_authid');
  IF dbc IS DISTINCT FROM expected AND rcur IS DISTINCT FROM expected THEN
    RAISE EXCEPTION 'drop refused: identity marker is not %', expected;
  END IF;
  -- 4. synthetic-only data
  IF EXISTS (SELECT 1 FROM users WHERE email NOT LIKE '%@example.test') THEN RAISE EXCEPTION 'drop refused: a non-synthetic user exists'; END IF;
  IF EXISTS (SELECT 1 FROM crm_contacts WHERE email IS NOT NULL AND email NOT LIKE '%.example.test') THEN RAISE EXCEPTION 'drop refused: a non-synthetic contact email exists'; END IF;
  IF EXISTS (SELECT 1 FROM crm_staff s JOIN users u ON u.id = s.user_id WHERE u.email NOT LIKE '%@example.test') THEN RAISE EXCEPTION 'drop refused: staff row for a non-synthetic user'; END IF;
  IF EXISTS (SELECT 1 FROM crm_credentials c JOIN users u ON u.id = c.user_id WHERE u.email NOT LIKE '%@example.test') THEN RAISE EXCEPTION 'drop refused: credential for a non-synthetic user'; END IF;
  IF EXISTS (SELECT 1 FROM crm_invites WHERE email NOT LIKE '%@example.test') THEN RAISE EXCEPTION 'drop refused: invite for a non-synthetic email'; END IF;
  -- 5. unexpected CRM data: exact approved counts, maxima, nothing newer than the approval
  SELECT count(*) INTO c1 FROM crm_credentials; SELECT count(*) INTO c2 FROM crm_invites; SELECT count(*) INTO c3 FROM crm_rate_limits;
  IF c1 <> exp_cred OR c2 <> exp_inv OR c3 <> exp_rl THEN
    RAISE EXCEPTION 'drop refused: row counts differ from the approval (credentials %, invites %, rate_limits %)', c1, c2, c3;
  END IF;
  SELECT count(*) INTO n FROM crm_companies; IF n > max_co THEN RAISE EXCEPTION 'drop refused: more companies than approved (%)', n; END IF;
  SELECT count(*) INTO n FROM crm_opportunities; IF n > max_op THEN RAISE EXCEPTION 'drop refused: more opportunities than approved (%)', n; END IF;
  IF EXISTS (SELECT 1 FROM crm_invites WHERE created_at > approved_iso) OR EXISTS (SELECT 1 FROM crm_credentials WHERE set_at > approved_iso)
     OR EXISTS (SELECT 1 FROM crm_rate_limits WHERE window_start > approved_epoch) THEN
    RAISE EXCEPTION 'drop refused: rows newer than the approval exist';
  END IF;
  -- 6. no dependents, no active crm_app work
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE contype = 'f' AND confrelid IN ('crm_credentials'::regclass, 'crm_invites'::regclass, 'crm_rate_limits'::regclass))
     OR EXISTS (SELECT 1 FROM pg_depend d JOIN pg_rewrite r ON r.oid = d.objid WHERE d.refobjid IN ('crm_credentials'::regclass, 'crm_invites'::regclass, 'crm_rate_limits'::regclass)) THEN
    RAISE EXCEPTION 'drop refused: something depends on these tables';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_stat_activity WHERE usename = 'crm_app' AND state <> 'idle' AND pid <> pg_backend_pid()) THEN
    RAISE EXCEPTION 'drop refused: crm_app has an active transaction';
  END IF;
  -- 7. all of the above and the drop are one transaction
  RAISE EXCEPTION 'DRY RUN OK: every guard passed; credentials=%, invites=%, rate_limits=%', c1, c2, c3;
END
$drop$;
