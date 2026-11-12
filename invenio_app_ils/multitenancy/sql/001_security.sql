-- SPDX-License-Identifier: MIT
-- Additive control-plane migration. Run transactionally as a migration role
-- permitted to CREATE ROLE / SET ROLE. Does not modify existing ILS records.
-- Role passwords are deliberately not provisioned here.
DO $migration$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'foslib_owner') THEN
    CREATE ROLE foslib_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'foslib_runtime') THEN
    CREATE ROLE foslib_runtime NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'foslib_auth_broker') THEN
    CREATE ROLE foslib_auth_broker NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
  END IF;
  IF EXISTS (SELECT FROM pg_roles WHERE rolname IN
      ('foslib_owner', 'foslib_runtime', 'foslib_auth_broker')
      AND (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolcanlogin)) THEN
    RAISE EXCEPTION 'Unsafe pre-existing Foslib role attributes';
  END IF;
  IF pg_has_role('foslib_runtime', 'foslib_owner', 'MEMBER') OR
     pg_has_role('foslib_runtime', 'foslib_auth_broker', 'MEMBER') OR
     pg_has_role('foslib_auth_broker', 'foslib_owner', 'MEMBER') OR
     pg_has_role('foslib_auth_broker', 'foslib_runtime', 'MEMBER') THEN
    RAISE EXCEPTION 'Unsafe pre-existing Foslib role memberships';
  END IF;
END
$migration$;

CREATE SCHEMA foslib AUTHORIZATION foslib_owner;
CREATE SCHEMA foslib_private AUTHORIZATION foslib_owner;
REVOKE ALL ON SCHEMA foslib, foslib_private FROM PUBLIC;
GRANT USAGE ON SCHEMA foslib TO foslib_runtime, foslib_auth_broker;

SET LOCAL ROLE foslib_owner;
ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

CREATE TABLE foslib_private.principals (
  user_id bigint PRIMARY KEY,
  active boolean NOT NULL DEFAULT true,
  platform_admin boolean NOT NULL DEFAULT false
);
-- user_id refers to the authenticated Invenio account; no second password store.
-- The deployment integration must add an FK after confirming the live account
-- table and use broker functions only after successful Invenio authentication.
CREATE TABLE foslib_private.tenants (
  id uuid PRIMARY KEY,
  slug text NOT NULL UNIQUE CHECK (slug ~ '^[a-z][a-z0-9-]{1,62}$'),
  name text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  active boolean NOT NULL DEFAULT true
);
CREATE TABLE foslib_private.memberships (
  tenant_id uuid NOT NULL REFERENCES foslib_private.tenants(id),
  user_id bigint NOT NULL REFERENCES foslib_private.principals(user_id),
  role text NOT NULL CHECK (role IN ('librarian', 'reader', 'worker')),
  active boolean NOT NULL DEFAULT true,
  PRIMARY KEY (tenant_id, user_id)
);
CREATE INDEX memberships_user ON foslib_private.memberships(user_id, tenant_id);
CREATE TABLE foslib_private.auth_sessions (
  token_hash bytea PRIMARY KEY CHECK (octet_length(token_hash) = 32),
  user_id bigint NOT NULL REFERENCES foslib_private.principals(user_id),
  expires_at timestamptz NOT NULL,
  revoked boolean NOT NULL DEFAULT false
);
CREATE INDEX auth_sessions_expiry ON foslib_private.auth_sessions(expires_at);
CREATE TABLE foslib_private.transaction_context (
  backend_pid integer PRIMARY KEY,
  transaction_id xid8 NOT NULL,
  token_hash bytea NOT NULL REFERENCES foslib_private.auth_sessions(token_hash),
  tenant_id uuid REFERENCES foslib_private.tenants(id)
);
-- One row per backend; xid8 prevents PID recycling and pool reuse from granting
-- access. Rows may outlive a transaction but cannot authenticate another one.
CREATE TABLE foslib_private.admin_audit (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  actor_user_id bigint NOT NULL,
  tenant_id uuid,
  action text NOT NULL,
  details jsonb NOT NULL DEFAULT '{}',
  occurred_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

-- Explicit owner policy allows only reviewed SECURITY DEFINER functions and
-- migrations to inspect private control tables. Runtime has no table grants.
DO $policies$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['principals','tenants','memberships','auth_sessions',
                          'transaction_context','admin_audit'] LOOP
    EXECUTE format('ALTER TABLE foslib_private.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE foslib_private.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY owner_access ON foslib_private.%I TO foslib_owner USING (true) WITH CHECK (true)', t);
  END LOOP;
END
$policies$;

CREATE FUNCTION foslib.issue_session(p_user_id bigint, p_hash bytea, p_expires timestamptz)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, foslib_private AS $function$
BEGIN
  IF p_expires <= clock_timestamp() OR p_expires > clock_timestamp() + interval '12 hours'
     OR NOT EXISTS (SELECT FROM foslib_private.principals WHERE user_id = p_user_id AND active) THEN
    RAISE EXCEPTION 'Invalid authentication session' USING ERRCODE = '28000';
  END IF;
  INSERT INTO foslib_private.auth_sessions(token_hash, user_id, expires_at)
    VALUES (p_hash, p_user_id, p_expires);
END
$function$;
GRANT EXECUTE ON FUNCTION foslib.issue_session(bigint, bytea, timestamptz) TO foslib_auth_broker;

CREATE FUNCTION foslib.revoke_session(p_hash bytea)
RETURNS void LANGUAGE sql SECURITY DEFINER
SET search_path = pg_catalog, foslib_private AS $function$
  UPDATE foslib_private.auth_sessions SET revoked = true WHERE token_hash = p_hash;
$function$;
GRANT EXECUTE ON FUNCTION foslib.revoke_session(bytea) TO foslib_auth_broker;

CREATE FUNCTION foslib.activate_context(p_token text, p_tenant uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, foslib_private AS $function$
DECLARE v_hash bytea; v_user bigint;
BEGIN
  IF p_token IS NULL OR length(p_token) <> 64 OR p_token !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'Invalid authentication context' USING ERRCODE = '28000';
  END IF;
  v_hash := sha256(convert_to(p_token, 'UTF8'));
  SELECT s.user_id INTO v_user FROM foslib_private.auth_sessions s
    JOIN foslib_private.principals p USING (user_id)
    WHERE s.token_hash = v_hash AND NOT s.revoked AND s.expires_at > clock_timestamp() AND p.active;
  IF v_user IS NULL OR (p_tenant IS NOT NULL AND NOT EXISTS (
      SELECT FROM foslib_private.memberships m JOIN foslib_private.tenants t ON t.id = m.tenant_id
      WHERE m.user_id = v_user AND m.tenant_id = p_tenant AND m.active AND t.active)) THEN
    RAISE EXCEPTION 'Unauthorised tenant context' USING ERRCODE = '28000';
  END IF;
  IF EXISTS (SELECT FROM foslib_private.transaction_context
       WHERE backend_pid = pg_backend_pid() AND transaction_id = pg_current_xact_id()) THEN
    RAISE EXCEPTION 'Tenant context already activated for this transaction' USING ERRCODE = '25001';
  END IF;
  INSERT INTO foslib_private.transaction_context(backend_pid, transaction_id, token_hash, tenant_id)
    VALUES (pg_backend_pid(), pg_current_xact_id(), v_hash, p_tenant)
    ON CONFLICT (backend_pid) DO UPDATE SET
      transaction_id = EXCLUDED.transaction_id, token_hash = EXCLUDED.token_hash, tenant_id = EXCLUDED.tenant_id;
END
$function$;
GRANT EXECUTE ON FUNCTION foslib.activate_context(text, uuid) TO foslib_runtime;

CREATE FUNCTION foslib.current_user_id()
RETURNS bigint LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, foslib_private AS $function$
  SELECT s.user_id FROM foslib_private.transaction_context c
    JOIN foslib_private.auth_sessions s ON s.token_hash = c.token_hash
    JOIN foslib_private.principals p ON p.user_id = s.user_id
    WHERE c.backend_pid = pg_backend_pid() AND c.transaction_id = pg_current_xact_id()
      AND NOT s.revoked AND s.expires_at > statement_timestamp() AND p.active;
$function$;
GRANT EXECUTE ON FUNCTION foslib.current_user_id() TO foslib_runtime;

CREATE FUNCTION foslib.current_tenant_id()
RETURNS uuid LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, foslib_private AS $function$
  SELECT c.tenant_id FROM foslib_private.transaction_context c
    JOIN foslib_private.memberships m ON m.tenant_id = c.tenant_id AND m.user_id = foslib.current_user_id()
    JOIN foslib_private.tenants t ON t.id = c.tenant_id
    WHERE c.backend_pid = pg_backend_pid() AND c.transaction_id = pg_current_xact_id()
      AND m.active AND t.active;
$function$;
GRANT EXECUTE ON FUNCTION foslib.current_tenant_id() TO foslib_runtime;

CREATE FUNCTION foslib.can_write()
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, foslib_private AS $function$
  SELECT EXISTS (SELECT FROM foslib_private.memberships
    WHERE tenant_id = foslib.current_tenant_id() AND user_id = foslib.current_user_id()
      AND active AND role IN ('librarian', 'worker'));
$function$;
GRANT EXECUTE ON FUNCTION foslib.can_write() TO foslib_runtime;

CREATE FUNCTION foslib.my_schools()
RETURNS TABLE (id uuid, slug text, name text, role text)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, foslib_private AS $function$
  SELECT t.id, t.slug, t.name, m.role FROM foslib_private.memberships m
    JOIN foslib_private.tenants t ON t.id = m.tenant_id
    WHERE m.user_id = foslib.current_user_id() AND m.active AND t.active ORDER BY t.slug;
$function$;
GRANT EXECUTE ON FUNCTION foslib.my_schools() TO foslib_runtime;

-- Administration is explicit: a platform principal does NOT bypass tenant RLS.
CREATE FUNCTION foslib.admin_set_school(p_id uuid, p_slug text, p_name text, p_active boolean)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, foslib_private AS $function$
DECLARE v_actor bigint := foslib.current_user_id();
BEGIN
  IF NOT EXISTS (SELECT FROM foslib_private.principals WHERE user_id = v_actor AND active AND platform_admin) THEN
    RAISE EXCEPTION 'Platform administrator required' USING ERRCODE = '42501';
  END IF;
  INSERT INTO foslib_private.tenants(id, slug, name, active) VALUES (p_id, p_slug, p_name, p_active)
    ON CONFLICT (id) DO UPDATE SET slug = EXCLUDED.slug, name = EXCLUDED.name, active = EXCLUDED.active;
  INSERT INTO foslib_private.admin_audit(actor_user_id, tenant_id, action, details)
    VALUES (v_actor, p_id, 'school.upsert', jsonb_build_object('slug', p_slug, 'name', p_name, 'active', p_active));
END
$function$;
GRANT EXECUTE ON FUNCTION foslib.admin_set_school(uuid, text, text, boolean) TO foslib_runtime;

REVOKE ALL ON ALL TABLES IN SCHEMA foslib_private FROM PUBLIC, foslib_runtime, foslib_auth_broker;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA foslib_private FROM PUBLIC, foslib_runtime, foslib_auth_broker;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA foslib, foslib_private FROM PUBLIC;
RESET ROLE;
