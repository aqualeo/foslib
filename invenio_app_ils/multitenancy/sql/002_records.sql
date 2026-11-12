-- SPDX-License-Identifier: MIT
-- Apply after 001, with explicit legacy assignments supplied by the operator.
-- All operational entities remain in Invenio's existing record/history tables.
SET LOCAL lock_timeout = '5s';

CREATE TABLE foslib_private.legacy_assignments (
  record_id uuid PRIMARY KEY REFERENCES public.records_metadata(id),
  tenant_id uuid NOT NULL REFERENCES foslib_private.tenants(id)
);
ALTER TABLE foslib_private.legacy_assignments OWNER TO foslib_owner;
ALTER TABLE foslib_private.legacy_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE foslib_private.legacy_assignments FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_access ON foslib_private.legacy_assignments TO foslib_owner USING (true) WITH CHECK (true);

-- Only these existing, explicitly named demonstration locations are assigned.
-- Any other pre-existing operational record must have an operator assignment.
INSERT INTO foslib_private.legacy_assignments(record_id, tenant_id)
 SELECT r.id, t.id FROM public.records_metadata r
 JOIN foslib_private.tenants t ON r.json->>'pid' = t.slug || '-library'
 WHERE r.json->>'$schema' LIKE '%/locations/location-%'
   AND t.slug IN ('alpha','beta');

INSERT INTO foslib_private.tenants(id,slug,name,active)
 VALUES ('00000000-0000-4000-8000-000000000001','legacy-quarantine','Preserved unclassified legacy records',false);

CREATE FUNCTION foslib_private.record_kind(p_json jsonb)
RETURNS text LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT CASE
  WHEN p_json->>'$schema' ~ '/documents/document-v' THEN 'document'
  WHEN p_json->>'$schema' ~ '/series/series-v' THEN 'series'
  WHEN p_json->>'$schema' ~ '/vocabularies/' THEN 'vocabulary'
  WHEN p_json->>'$schema' ~ '/licenses/' THEN 'license'
  WHEN p_json->>'$schema' ~ '/items/item-v' THEN 'item'
  WHEN p_json->>'$schema' ~ '/loans/loan-v' THEN 'loan'
  WHEN p_json->>'$schema' ~ '/internal_locations/' THEN 'internal_location'
  WHEN p_json->>'$schema' ~ '/locations/location-v' THEN 'location'
  WHEN p_json->>'$schema' ~ '/eitems/' THEN 'eitem'
  WHEN p_json->>'$schema' ~ '/acq_orders/' THEN 'order'
  WHEN p_json->>'$schema' ~ '/providers/' THEN 'provider'
  WHEN p_json->>'$schema' ~ '/ill_borrowing_requests/' THEN 'borrowing_request'
  WHEN p_json->>'$schema' ~ '/document_requests/' THEN 'document_request'
  WHEN p_json->>'$schema' ~ '/foslib/member-v1.json$' THEN 'member'
  WHEN p_json->>'$schema' ~ '/foslib/hold-v1.json$' THEN 'hold'
  WHEN p_json->>'$schema' ~ '/foslib/fine-v1.json$' THEN 'fine'
  WHEN p_json->>'$schema' ~ '/foslib/payment-v1.json$' THEN 'payment'
  WHEN p_json->>'$schema' ~ '/foslib/event-v1.json$' THEN 'event'
  ELSE 'quarantine' END;
$$;
ALTER FUNCTION foslib_private.record_kind(jsonb) OWNER TO foslib_owner;

ALTER TABLE public.records_metadata
 ADD COLUMN tenant_id uuid REFERENCES foslib_private.tenants(id),
 ADD COLUMN record_kind text,
 ADD COLUMN record_pid text,
 ADD COLUMN document_pid text,
 ADD COLUMN document_kind text GENERATED ALWAYS AS (CASE WHEN document_pid IS NOT NULL THEN 'document' END) STORED,
 ADD COLUMN copy_pid text,
 ADD COLUMN copy_kind text GENERATED ALWAYS AS (CASE WHEN copy_pid IS NOT NULL THEN 'item' END) STORED,
 ADD COLUMN member_pid text,
 ADD COLUMN member_kind text GENERATED ALWAYS AS (CASE WHEN member_pid IS NOT NULL THEN 'member' END) STORED,
 ADD COLUMN location_pid text,
 ADD COLUMN location_kind text GENERATED ALWAYS AS (CASE WHEN location_pid IS NOT NULL THEN 'location' END) STORED,
 ADD COLUMN internal_location_pid text,
 ADD COLUMN internal_location_kind text GENERATED ALWAYS AS (CASE WHEN internal_location_pid IS NOT NULL THEN 'internal_location' END) STORED,
 ADD COLUMN loan_pid text,
 ADD COLUMN loan_kind text GENERATED ALWAYS AS (CASE WHEN loan_pid IS NOT NULL THEN 'loan' END) STORED,
 ADD COLUMN fine_pid text,
 ADD COLUMN fine_kind text GENERATED ALWAYS AS (CASE WHEN fine_pid IS NOT NULL THEN 'fine' END) STORED;

UPDATE public.records_metadata SET
 record_kind=foslib_private.record_kind(json), record_pid=json->>'pid';
UPDATE public.records_metadata r SET tenant_id=a.tenant_id
 FROM foslib_private.legacy_assignments a WHERE a.record_id=r.id;
UPDATE public.records_metadata SET tenant_id='00000000-0000-4000-8000-000000000001'
 WHERE record_kind='quarantine';
DO $$ BEGIN
 IF EXISTS (SELECT FROM public.records_metadata WHERE
   record_kind NOT IN ('document','series','vocabulary','license') AND tenant_id IS NULL) THEN
   RAISE EXCEPTION 'Unassigned legacy operational records; ownership must be reviewed before migration';
 END IF;
END $$;

ALTER TABLE public.records_metadata
 ALTER COLUMN record_kind SET NOT NULL,
 ADD CONSTRAINT record_scope CHECK (
   (record_kind IN ('document','series','vocabulary','license') AND tenant_id IS NULL) OR
   (record_kind NOT IN ('document','series','vocabulary','license') AND tenant_id IS NOT NULL)),
 ADD CONSTRAINT record_identity UNIQUE(record_kind,record_pid),
 ADD CONSTRAINT tenant_record_identity UNIQUE(tenant_id,record_kind,record_pid),
 ADD CONSTRAINT tenant_record_uuid UNIQUE(tenant_id,id),
 ADD CONSTRAINT copy_document_fk FOREIGN KEY(document_kind,document_pid)
   REFERENCES public.records_metadata(record_kind,record_pid) DEFERRABLE INITIALLY DEFERRED,
 ADD CONSTRAINT tenant_copy_fk FOREIGN KEY(tenant_id,copy_kind,copy_pid)
   REFERENCES public.records_metadata(tenant_id,record_kind,record_pid) DEFERRABLE INITIALLY DEFERRED,
 ADD CONSTRAINT tenant_member_fk FOREIGN KEY(tenant_id,member_kind,member_pid)
   REFERENCES public.records_metadata(tenant_id,record_kind,record_pid) DEFERRABLE INITIALLY DEFERRED,
 ADD CONSTRAINT tenant_location_fk FOREIGN KEY(tenant_id,location_kind,location_pid)
   REFERENCES public.records_metadata(tenant_id,record_kind,record_pid) DEFERRABLE INITIALLY DEFERRED,
 ADD CONSTRAINT tenant_internal_location_fk FOREIGN KEY(tenant_id,internal_location_kind,internal_location_pid)
   REFERENCES public.records_metadata(tenant_id,record_kind,record_pid) DEFERRABLE INITIALLY DEFERRED,
 ADD CONSTRAINT tenant_loan_fk FOREIGN KEY(tenant_id,loan_kind,loan_pid)
   REFERENCES public.records_metadata(tenant_id,record_kind,record_pid) DEFERRABLE INITIALLY DEFERRED,
 ADD CONSTRAINT tenant_fine_fk FOREIGN KEY(tenant_id,fine_kind,fine_pid)
   REFERENCES public.records_metadata(tenant_id,record_kind,record_pid) DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX records_tenant_kind ON public.records_metadata(tenant_id,record_kind,created DESC);
CREATE UNIQUE INDEX copy_barcode_per_school ON public.records_metadata(tenant_id,(json->>'barcode')) WHERE record_kind='item' AND json IS NOT NULL;
CREATE UNIQUE INDEX member_code_per_school ON public.records_metadata(tenant_id,(json->>'code')) WHERE record_kind='member' AND json IS NOT NULL;
CREATE UNIQUE INDEX single_active_loan_per_copy ON public.records_metadata(tenant_id,copy_pid)
 WHERE record_kind='loan' AND json->>'state'='ITEM_ON_LOAN';
CREATE UNIQUE INDEX single_active_hold_per_member ON public.records_metadata(tenant_id,document_pid,member_pid)
 WHERE record_kind='hold' AND json->>'status'='pending';
CREATE INDEX catalogue_title_search ON public.records_metadata USING gin(to_tsvector('simple',coalesce(json->>'title','')))
 WHERE record_kind='document';

CREATE FUNCTION foslib_private.scope_record()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,foslib_private AS $$
DECLARE k text;
BEGIN
 IF TG_OP='UPDATE' AND OLD.record_kind IN ('event','payment') THEN
   RAISE EXCEPTION 'Circulation events and payments are append-only' USING ERRCODE='42501';
 END IF;
 IF TG_OP='UPDATE' AND NEW.json IS NULL THEN
   -- Tombstones retain identity/ownership so history and references stay secure.
   NEW.tenant_id:=OLD.tenant_id; NEW.record_kind:=OLD.record_kind; NEW.record_pid:=OLD.record_pid;
   RETURN NEW;
 END IF;
 k:=foslib_private.record_kind(NEW.json);
 IF TG_OP='UPDATE' AND (NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR
    k IS DISTINCT FROM OLD.record_kind OR NEW.json->>'pid' IS DISTINCT FROM OLD.record_pid) THEN
   RAISE EXCEPTION 'Record ownership and identity are immutable' USING ERRCODE='42501';
 END IF;
 NEW.record_kind:=k; NEW.record_pid:=NEW.json->>'pid';
 IF k IN ('document','series','vocabulary','license') THEN
   IF NEW.tenant_id IS NOT NULL THEN RAISE EXCEPTION 'Shared metadata cannot be school-owned'; END IF;
 ELSE
   NEW.tenant_id:=coalesce(NEW.tenant_id,foslib.current_tenant_id());
   IF NEW.tenant_id IS NULL OR k='quarantine' OR NEW.record_pid IS NULL THEN
     RAISE EXCEPTION 'School record requires verified ownership and a known schema' USING ERRCODE='42501';
   END IF;
 END IF;
 NEW.document_pid:=NEW.json->>'document_pid';
 NEW.copy_pid:=coalesce(NEW.json#>>'{item_pid,value}',NEW.json->>'copy_pid');
 NEW.member_pid:=NEW.json->>'patron_pid';
 NEW.location_pid:=coalesce(NEW.json->>'location_pid',NEW.json->>'transaction_location_pid');
 NEW.internal_location_pid:=NEW.json->>'internal_location_pid';
 NEW.loan_pid:=NEW.json->>'loan_pid'; NEW.fine_pid:=NEW.json->>'fine_pid';
 IF k='item' AND (NEW.document_pid IS NULL OR NEW.internal_location_pid IS NULL OR coalesce(NEW.json->>'barcode','')='') THEN
   RAISE EXCEPTION 'Copy requires book, location and barcode';
 END IF;
 IF k='member' AND (coalesce(NEW.json->>'code','')='' OR coalesce(NEW.json->>'name','')='') THEN
   RAISE EXCEPTION 'Member requires code and name';
 END IF;
 IF k='loan' AND (NEW.copy_pid IS NULL OR NEW.member_pid IS NULL OR NEW.document_pid IS NULL OR NEW.location_pid IS NULL) THEN
   RAISE EXCEPTION 'Loan requires school copy, member, book and location';
 END IF;
 IF k='hold' AND (NEW.member_pid IS NULL OR NEW.document_pid IS NULL) THEN
   RAISE EXCEPTION 'Hold requires book and school member';
 END IF;
 IF k='fine' AND (NEW.loan_pid IS NULL OR NEW.member_pid IS NULL OR coalesce((NEW.json->>'amount_cents')::integer,0)<=0) THEN
   RAISE EXCEPTION 'Fine requires loan, member and positive amount';
 END IF;
 IF k='payment' AND (NEW.fine_pid IS NULL OR coalesce((NEW.json->>'amount_cents')::integer,0)<=0) THEN
   RAISE EXCEPTION 'Payment requires fine and positive amount';
 END IF;
 RETURN NEW;
END $$;
ALTER FUNCTION foslib_private.scope_record() OWNER TO foslib_owner;
CREATE TRIGGER scope_record BEFORE INSERT OR UPDATE ON public.records_metadata FOR EACH ROW EXECUTE FUNCTION foslib_private.scope_record();

ALTER TABLE public.records_metadata_version ADD COLUMN tenant_id uuid, ADD COLUMN record_kind text,
 ADD COLUMN written_in_xid xid8 NOT NULL DEFAULT pg_current_xact_id();
UPDATE public.records_metadata_version v SET tenant_id=r.tenant_id,record_kind=r.record_kind FROM public.records_metadata r WHERE r.id=v.id;
UPDATE public.records_metadata_version SET tenant_id='00000000-0000-4000-8000-000000000001',record_kind='quarantine' WHERE record_kind IS NULL;
ALTER TABLE public.records_metadata_version ALTER COLUMN record_kind SET NOT NULL;
ALTER TABLE public.records_metadata_version ADD CONSTRAINT history_scope CHECK (
 (record_kind IN ('document','series','vocabulary','license') AND tenant_id IS NULL) OR
 (record_kind NOT IN ('document','series','vocabulary','license') AND tenant_id IS NOT NULL));
CREATE INDEX history_tenant_record ON public.records_metadata_version(tenant_id,id,transaction_id);
CREATE FUNCTION foslib_private.scope_history() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,foslib_private AS $$
BEGIN
 IF TG_OP='UPDATE' AND OLD.written_in_xid<>pg_current_xact_id() AND
  (NEW.json IS DISTINCT FROM OLD.json OR NEW.version_id IS DISTINCT FROM OLD.version_id OR NEW.updated IS DISTINCT FROM OLD.updated OR NEW.operation_type IS DISTINCT FROM OLD.operation_type) THEN
   RAISE EXCEPTION 'Committed record history is immutable' USING ERRCODE='42501';
 END IF;
 SELECT r.tenant_id,r.record_kind INTO NEW.tenant_id,NEW.record_kind FROM public.records_metadata r WHERE r.id=NEW.id;
 IF NEW.record_kind IS NULL THEN RAISE EXCEPTION 'History requires an existing scoped record'; END IF;
 RETURN NEW;
END $$;
ALTER FUNCTION foslib_private.scope_history() OWNER TO foslib_owner;
CREATE TRIGGER scope_history BEFORE INSERT OR UPDATE ON public.records_metadata_version FOR EACH ROW EXECUTE FUNCTION foslib_private.scope_history();

ALTER TABLE public.records_metadata OWNER TO foslib_owner;
ALTER TABLE public.records_metadata_version OWNER TO foslib_owner;
GRANT SELECT ON public.accounts_user TO foslib_owner;
ALTER TABLE foslib_private.principals ADD CONSTRAINT authenticated_account_fk FOREIGN KEY(user_id) REFERENCES public.accounts_user(id);
CREATE OR REPLACE FUNCTION foslib.current_user_id()
RETURNS bigint LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,foslib_private AS $$
 SELECT s.user_id FROM foslib_private.transaction_context c
 JOIN foslib_private.auth_sessions s ON s.token_hash=c.token_hash
 JOIN foslib_private.principals p ON p.user_id=s.user_id
 JOIN public.accounts_user a ON a.id=s.user_id
 WHERE c.backend_pid=pg_backend_pid() AND c.transaction_id=pg_current_xact_id()
   AND NOT s.revoked AND s.expires_at>statement_timestamp() AND p.active AND a.active;
$$;
ALTER FUNCTION foslib.current_user_id() OWNER TO foslib_owner;
REVOKE EXECUTE ON FUNCTION foslib.current_user_id() FROM PUBLIC;

DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['records_metadata','records_metadata_version'] LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY owner_access ON public.%I TO foslib_owner USING(true) WITH CHECK(true)',t);
  EXECUTE format('CREATE POLICY tenant_read ON public.%I FOR SELECT TO foslib_runtime USING (tenant_id IS NULL OR tenant_id=foslib.current_tenant_id())',t);
  EXECUTE format('CREATE POLICY tenant_insert ON public.%I FOR INSERT TO foslib_runtime WITH CHECK (tenant_id=foslib.current_tenant_id() AND foslib.can_write())',t);
  EXECUTE format('CREATE POLICY tenant_update ON public.%I FOR UPDATE TO foslib_runtime USING (tenant_id=foslib.current_tenant_id() AND foslib.can_write()) WITH CHECK (tenant_id=foslib.current_tenant_id() AND foslib.can_write())',t);
 END LOOP;
END $$;
-- School rows use soft deletion; runtime cannot physically erase history.
GRANT USAGE ON SCHEMA public TO foslib_runtime;
GRANT SELECT ON public.records_metadata,public.records_metadata_version TO foslib_runtime;
GRANT INSERT(id,json,version_id,created,updated), UPDATE(json,version_id,updated) ON public.records_metadata TO foslib_runtime;
GRANT INSERT(id,json,version_id,created,updated,transaction_id,end_transaction_id,operation_type), UPDATE(json,version_id,updated,end_transaction_id,operation_type) ON public.records_metadata_version TO foslib_runtime;

-- Authentication remains global and read-only for the operational runtime.
GRANT SELECT ON public.accounts_user,public.accounts_role,public.accounts_userrole,
 public.access_actionsroles,public.access_actionsusers,public.access_actionssystemroles TO foslib_runtime;
GRANT SELECT,INSERT,UPDATE ON public.accounts_user_login_information,public.accounts_user_session_activity TO foslib_runtime;
GRANT DELETE ON public.accounts_user_session_activity TO foslib_runtime;

CREATE FUNCTION foslib_private.can_read_record(p_id uuid) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,foslib_private AS $$
 SELECT EXISTS(SELECT FROM public.records_metadata WHERE id=p_id AND (tenant_id IS NULL OR tenant_id=foslib.current_tenant_id()));
$$;
CREATE FUNCTION foslib_private.can_mint_record(p_id uuid) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,foslib_private AS $$
 SELECT foslib.can_write() AND NOT EXISTS(SELECT FROM public.records_metadata WHERE id=p_id AND tenant_id IS DISTINCT FROM foslib.current_tenant_id());
$$;
ALTER FUNCTION foslib_private.can_read_record(uuid) OWNER TO foslib_owner;
ALTER FUNCTION foslib_private.can_mint_record(uuid) OWNER TO foslib_owner;
-- Functions used by policies expose only decisions, not private rows.
GRANT USAGE ON SCHEMA foslib_private TO foslib_runtime;
GRANT EXECUTE ON FUNCTION foslib_private.can_read_record(uuid),foslib_private.can_mint_record(uuid) TO foslib_runtime;
ALTER TABLE public.pidstore_pid OWNER TO foslib_owner;
ALTER TABLE public.pidstore_pid ADD COLUMN tenant_id uuid DEFAULT foslib.current_tenant_id() REFERENCES foslib_private.tenants(id);
UPDATE public.pidstore_pid p SET tenant_id=r.tenant_id FROM public.records_metadata r WHERE r.id=p.object_uuid;
ALTER TABLE public.pidstore_pid ADD CONSTRAINT tenant_pid_record_fk FOREIGN KEY(tenant_id,object_uuid)
 REFERENCES public.records_metadata(tenant_id,id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE public.pidstore_pid ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.pidstore_pid FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_access ON public.pidstore_pid TO foslib_owner USING(true) WITH CHECK(true);
CREATE POLICY scoped_pid_read ON public.pidstore_pid FOR SELECT TO foslib_runtime USING(object_type='rec' AND (tenant_id=foslib.current_tenant_id() OR foslib_private.can_read_record(object_uuid)));
CREATE POLICY scoped_pid_mint ON public.pidstore_pid FOR INSERT TO foslib_runtime WITH CHECK(tenant_id=foslib.current_tenant_id() AND object_type='rec' AND object_uuid IS NOT NULL AND foslib_private.can_mint_record(object_uuid));
GRANT SELECT,INSERT ON public.pidstore_pid TO foslib_runtime;
GRANT SELECT,INSERT ON public.pidstore_recid TO foslib_runtime;
GRANT USAGE,SELECT ON SEQUENCE public.pidstore_pid_id_seq,public.pidstore_recid_recid_seq TO foslib_runtime;

ALTER TABLE public.transaction ADD COLUMN tenant_id uuid DEFAULT foslib.current_tenant_id();
ALTER TABLE public.transaction OWNER TO foslib_owner;
ALTER TABLE public.transaction ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.transaction FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_access ON public.transaction TO foslib_owner USING(true) WITH CHECK(true);
CREATE POLICY tenant_read ON public.transaction FOR SELECT TO foslib_runtime USING(tenant_id=foslib.current_tenant_id());
CREATE POLICY tenant_insert ON public.transaction FOR INSERT TO foslib_runtime WITH CHECK(tenant_id=foslib.current_tenant_id() AND foslib.can_write());
GRANT SELECT,INSERT ON public.transaction TO foslib_runtime;
GRANT USAGE,SELECT ON SEQUENCE public.transaction_id_seq TO foslib_runtime;
ALTER TABLE public.notifications_logs ADD COLUMN tenant_id uuid NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001' REFERENCES foslib_private.tenants(id);
ALTER TABLE public.notifications_logs ALTER COLUMN tenant_id SET DEFAULT foslib.current_tenant_id();
ALTER TABLE public.notifications_logs OWNER TO foslib_owner;
ALTER TABLE public.notifications_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.notifications_logs FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_access ON public.notifications_logs TO foslib_owner USING(true) WITH CHECK(true);
CREATE POLICY tenant_read ON public.notifications_logs FOR SELECT TO foslib_runtime USING(tenant_id=foslib.current_tenant_id());
CREATE POLICY tenant_insert ON public.notifications_logs FOR INSERT TO foslib_runtime WITH CHECK(tenant_id=foslib.current_tenant_id() AND foslib.can_write());
GRANT SELECT,INSERT ON public.notifications_logs TO foslib_runtime;
GRANT USAGE,SELECT ON SEQUENCE public.notifications_logs_id_seq TO foslib_runtime;

REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA foslib_private FROM PUBLIC;
