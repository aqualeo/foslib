-- SPDX-License-Identifier: MIT
CREATE FUNCTION foslib_private.canonical_isbn(p_value text) RETURNS text
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE s text:=upper(regexp_replace(p_value,'[[:space:]-]','','g')); n integer:=0; d integer; i integer;
BEGIN
 IF s ~ '^[0-9]{9}[0-9X]$' THEN
  FOR i IN 1..10 LOOP
   d:=CASE WHEN substr(s,i,1)='X' THEN 10 ELSE substr(s,i,1)::integer END;
   n:=n+(11-i)*d;
  END LOOP;
  IF n%11<>0 THEN RETURN NULL; END IF;
  s:='978'||left(s,9); n:=0;
  FOR i IN 1..12 LOOP n:=n+substr(s,i,1)::integer*CASE WHEN i%2=1 THEN 1 ELSE 3 END; END LOOP;
  s:=s||((10-n%10)%10)::text;
 END IF;
 IF s !~ '^97[89][0-9]{10}$' OR s IS NULL THEN RETURN NULL; END IF;
 n:=0;
 FOR i IN 1..13 LOOP n:=n+substr(s,i,1)::integer*CASE WHEN i%2=1 THEN 1 ELSE 3 END; END LOOP;
 RETURN CASE WHEN n%10=0 THEN s ELSE NULL END;
END $$;
ALTER FUNCTION foslib_private.canonical_isbn(text) OWNER TO foslib_owner;
CREATE TABLE foslib_private.catalogue_isbns (
 isbn13 text PRIMARY KEY,
 record_id uuid NOT NULL REFERENCES public.records_metadata(id) ON DELETE CASCADE
);
ALTER TABLE foslib_private.catalogue_isbns OWNER TO foslib_owner;
ALTER TABLE foslib_private.catalogue_isbns ENABLE ROW LEVEL SECURITY;
ALTER TABLE foslib_private.catalogue_isbns FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_access ON foslib_private.catalogue_isbns TO foslib_owner USING(true) WITH CHECK(true);
INSERT INTO foslib_private.catalogue_isbns(isbn13,record_id)
 SELECT DISTINCT foslib_private.canonical_isbn(i->>'value'),r.id
 FROM public.records_metadata r CROSS JOIN LATERAL jsonb_array_elements(coalesce(r.json->'identifiers','[]')) i
 WHERE r.record_kind='document' AND upper(i->>'scheme')='ISBN' AND foslib_private.canonical_isbn(i->>'value') IS NOT NULL;
CREATE FUNCTION foslib_private.index_catalogue_isbns() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,foslib_private AS $$
DECLARE i jsonb; canonical text;
BEGIN
 IF NEW.record_kind<>'document' THEN RETURN NEW; END IF;
 DELETE FROM foslib_private.catalogue_isbns WHERE record_id=NEW.id;
 FOR i IN SELECT * FROM jsonb_array_elements(coalesce(NEW.json->'identifiers','[]')) LOOP
  IF upper(i->>'scheme')='ISBN' THEN
   canonical:=foslib_private.canonical_isbn(i->>'value');
   IF canonical IS NULL THEN RAISE EXCEPTION 'Invalid ISBN checksum' USING ERRCODE='23514'; END IF;
   IF NOT EXISTS(SELECT FROM foslib_private.catalogue_isbns WHERE isbn13=canonical AND record_id=NEW.id) THEN
    INSERT INTO foslib_private.catalogue_isbns VALUES(canonical,NEW.id);
   END IF;
  END IF;
 END LOOP;
 RETURN NEW;
END $$;
ALTER FUNCTION foslib_private.index_catalogue_isbns() OWNER TO foslib_owner;
CREATE TRIGGER index_catalogue_isbns AFTER INSERT OR UPDATE OF json ON public.records_metadata
 FOR EACH ROW EXECUTE FUNCTION foslib_private.index_catalogue_isbns();

CREATE FUNCTION foslib.is_platform_admin() RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,foslib_private AS $$
 SELECT EXISTS(SELECT FROM foslib_private.principals WHERE user_id=foslib.current_user_id() AND active AND platform_admin);
$$;
ALTER FUNCTION foslib.is_platform_admin() OWNER TO foslib_owner;
CREATE FUNCTION foslib_private.require_admin() RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,foslib_private AS $$
BEGIN
 IF NOT foslib.is_platform_admin() THEN RAISE EXCEPTION 'Platform administrator required' USING ERRCODE='42501'; END IF;
 RETURN foslib.current_user_id();
END $$;
ALTER FUNCTION foslib_private.require_admin() OWNER TO foslib_owner;

CREATE FUNCTION foslib.admin_save_document(p_id uuid,p_json jsonb) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,foslib_private AS $$
DECLARE actor bigint:=foslib_private.require_admin(); previous jsonb;
BEGIN
 IF foslib_private.record_kind(p_json)<>'document' OR length(coalesce(p_json->>'title','')) NOT BETWEEN 1 AND 1000
   OR jsonb_typeof(p_json->'authors') IS DISTINCT FROM 'array' OR jsonb_array_length(p_json->'authors')=0
   OR coalesce(p_json->>'pid','')='' THEN
   RAISE EXCEPTION 'Invalid bibliographic metadata' USING ERRCODE='23514';
 END IF;
 SELECT json INTO previous FROM public.records_metadata WHERE id=p_id FOR UPDATE;
 IF FOUND THEN
  IF foslib_private.record_kind(previous)<>'document' THEN RAISE EXCEPTION 'Shared document required' USING ERRCODE='42501'; END IF;
  UPDATE public.records_metadata SET json=p_json,version_id=version_id+1,updated=clock_timestamp() WHERE id=p_id;
 ELSE
  INSERT INTO public.records_metadata(id,json,version_id,created,updated) VALUES(p_id,p_json,1,clock_timestamp(),clock_timestamp());
  INSERT INTO public.pidstore_pid(pid_type,pid_value,status,object_type,object_uuid,created,updated,tenant_id)
   VALUES('docid',p_json->>'pid','R','rec',p_id,clock_timestamp(),clock_timestamp(),NULL);
 END IF;
 INSERT INTO foslib_private.admin_audit(actor_user_id,action,details)
 VALUES(actor,'catalogue.save',jsonb_build_object('record_id',p_id,'before',previous,'after',p_json));
 RETURN p_id;
END $$;
ALTER FUNCTION foslib.admin_save_document(uuid,jsonb) OWNER TO foslib_owner;

CREATE FUNCTION foslib.admin_schools() RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,foslib_private AS $$
DECLARE actor bigint:=foslib_private.require_admin(); result jsonb;
BEGIN
 SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY slug),'[]') INTO result FROM foslib_private.tenants t WHERE slug<>'legacy-quarantine';
 INSERT INTO foslib_private.admin_audit(actor_user_id,action) VALUES(actor,'school.list');
 RETURN result;
END $$;
ALTER FUNCTION foslib.admin_schools() OWNER TO foslib_owner;

CREATE FUNCTION foslib.admin_membership(p_email text,p_tenant uuid,p_role text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,foslib_private AS $$
DECLARE actor bigint:=foslib_private.require_admin(); target bigint;
BEGIN
 IF p_role NOT IN ('librarian','reader') THEN RAISE EXCEPTION 'Invalid membership role' USING ERRCODE='23514'; END IF;
 SELECT id INTO target FROM public.accounts_user WHERE lower(email)=lower(p_email) AND active;
 IF target IS NULL THEN RAISE EXCEPTION 'Active authenticated account not found' USING ERRCODE='23514'; END IF;
 INSERT INTO foslib_private.principals(user_id) VALUES(target) ON CONFLICT(user_id) DO NOTHING;
 INSERT INTO foslib_private.memberships(tenant_id,user_id,role) VALUES(p_tenant,target,p_role)
 ON CONFLICT(tenant_id,user_id) DO UPDATE SET role=excluded.role,active=true;
 INSERT INTO foslib_private.admin_audit(actor_user_id,tenant_id,action,details)
 VALUES(actor,p_tenant,'membership.grant',jsonb_build_object('user_id',target,'role',p_role));
END $$;
ALTER FUNCTION foslib.admin_membership(text,uuid,text) OWNER TO foslib_owner;

CREATE FUNCTION foslib.admin_audit_log() RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,foslib_private AS $$
DECLARE actor bigint:=foslib_private.require_admin(); result jsonb;
BEGIN
 SELECT coalesce(jsonb_agg(to_jsonb(a)),'[]') INTO result FROM
 (SELECT id,actor_user_id,tenant_id,action,occurred_at FROM foslib_private.admin_audit ORDER BY id DESC LIMIT 200) a;
 INSERT INTO foslib_private.admin_audit(actor_user_id,action) VALUES(actor,'audit.read');
 RETURN result;
END $$;
ALTER FUNCTION foslib.admin_audit_log() OWNER TO foslib_owner;

GRANT EXECUTE ON FUNCTION foslib.is_platform_admin(),foslib.admin_save_document(uuid,jsonb),
 foslib.admin_schools(),foslib.admin_membership(text,uuid,text),foslib.admin_audit_log() TO foslib_runtime;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA foslib,foslib_private FROM PUBLIC;
