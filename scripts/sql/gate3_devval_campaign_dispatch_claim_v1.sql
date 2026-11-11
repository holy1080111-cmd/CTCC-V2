-- CTCC D/V dispatch claim v1. Apply once, after the exact N142 custody SQL,
-- as that schema's owner in the separately administered PostgreSQL 17 evidence
-- database. No roles or grants are created. An administrator must separately
-- grant a third restricted, direct LOGIN schema USAGE and EXECUTE on exactly
-- read_campaign(text), claim_dispatch(text,text), read_dispatch_claim(text).
-- The collector must have no publish/ack/table rights. A root-binding digest
-- has meaning only with independently retained canonical full-root bytes.
-- SQL timestamps alone do not prove trusted UTC or Gate 3 availability.

BEGIN;

DO $ctcc_dispatch_preflight$
DECLARE
  installer oid := pg_catalog.to_regrole(current_user)::oid;
BEGIN
  IF session_user IS DISTINCT FROM current_user
    OR pg_catalog.current_setting('server_version_num')::integer
      NOT BETWEEN 170000 AND 179999
    OR pg_catalog.current_setting('server_encoding') <> 'UTF8'
    OR pg_catalog.to_regclass('public.alembic_version') IS NOT NULL
    OR (SELECT n.nspowner FROM pg_catalog.pg_namespace n
        WHERE n.nspname='ctcc_devval_custody') IS DISTINCT FROM installer
    OR (SELECT pg_catalog.count(*) FROM pg_catalog.pg_class c
        WHERE c.oid IN (
          'ctcc_devval_custody.campaigns'::pg_catalog.regclass,
          'ctcc_devval_custody.acknowledgements'::pg_catalog.regclass
        ) AND c.relkind='r' AND c.relowner=installer) <> 2
    OR (SELECT pg_catalog.count(*) FROM pg_catalog.pg_proc p
        WHERE p.oid IN (
          'ctcc_devval_custody._require_role(text)'::pg_catalog.regprocedure,
          'ctcc_devval_custody._durable(boolean)'::pg_catalog.regprocedure,
          'ctcc_devval_custody.publish_campaign(text,text)'::pg_catalog.regprocedure,
          'ctcc_devval_custody.acknowledge_campaign(text)'::pg_catalog.regprocedure,
          'ctcc_devval_custody.read_campaign(text)'::pg_catalog.regprocedure
        ) AND p.proowner=installer AND p.prosecdef) <> 5
  THEN
    RAISE EXCEPTION 'ctcc_devval_dispatch_base_identity_denied';
  END IF;
END
$ctcc_dispatch_preflight$;

CREATE TABLE ctcc_devval_custody.dispatch_claims (
  campaign_sha256 varchar(64) COLLATE "C" PRIMARY KEY
    REFERENCES ctcc_devval_custody.acknowledgements(campaign_sha256),
  root_binding_sha256 varchar(64) COLLATE "C" NOT NULL UNIQUE,
  collector_role_oid oid NOT NULL,
  dispatch_xid text COLLATE "C" NOT NULL,
  claimed_at timestamptz NOT NULL,
  CONSTRAINT ck_devval_dispatch_campaign_sha
    CHECK (campaign_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT ck_devval_dispatch_root_sha
    CHECK (root_binding_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT ck_devval_dispatch_xid
    CHECK (dispatch_xid ~ '^[1-9][0-9]*$')
);
REVOKE ALL ON TABLE ctcc_devval_custody.dispatch_claims FROM PUBLIC;

CREATE TRIGGER ctcc_devval_dispatch_immutable
  BEFORE UPDATE OR DELETE ON ctcc_devval_custody.dispatch_claims
  FOR EACH ROW EXECUTE FUNCTION ctcc_devval_custody._immutable();
CREATE TRIGGER ctcc_devval_dispatch_no_truncate
  BEFORE TRUNCATE ON ctcc_devval_custody.dispatch_claims
  FOR EACH STATEMENT EXECUTE FUNCTION ctcc_devval_custody._immutable();

CREATE FUNCTION ctcc_devval_custody._require_collector()
RETURNS oid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $ctcc_dispatch_role$
DECLARE
  actor oid;
  direct_count bigint;
  direct_safe boolean;
BEGIN
  -- N142's read guard checks direct login, no membership or CREATE rights,
  -- direct read_campaign EXECUTE, and no base-table or publisher rights.
  actor := ctcc_devval_custody._require_role('read');
  SELECT pg_catalog.count(DISTINCT p.oid),
         pg_catalog.bool_and(NOT a.is_grantable)
    INTO direct_count,direct_safe
    FROM pg_catalog.pg_proc p,
      LATERAL pg_catalog.aclexplode(
        COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))
      ) a
    WHERE p.oid IN (
      'ctcc_devval_custody.claim_dispatch(text,text)'::pg_catalog.regprocedure,
      'ctcc_devval_custody.read_dispatch_claim(text)'::pg_catalog.regprocedure
    ) AND a.grantee=actor AND a.privilege_type='EXECUTE';
  IF direct_count <> 2 OR direct_safe IS DISTINCT FROM true
    OR pg_catalog.has_function_privilege(
      actor,
      'ctcc_devval_custody.acknowledge_campaign(text)'::pg_catalog.regprocedure,
      'EXECUTE'
    )
    OR pg_catalog.has_table_privilege(
      actor,'ctcc_devval_custody.dispatch_claims'::pg_catalog.regclass,
      'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN'
    )
    OR pg_catalog.has_any_column_privilege(
      actor,'ctcc_devval_custody.dispatch_claims'::pg_catalog.regclass,
      'SELECT,INSERT,UPDATE,REFERENCES'
    )
    OR pg_catalog.current_setting('session_replication_role') <> 'origin'
    OR pg_catalog.pg_is_in_recovery()
  THEN
    RAISE EXCEPTION 'ctcc_devval_collector_role_denied';
  END IF;
  RETURN actor;
END
$ctcc_dispatch_role$;

CREATE FUNCTION ctcc_devval_custody.claim_dispatch(
  p_campaign_sha256 text, p_root_binding_sha256 text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $ctcc_dispatch_claim$
DECLARE
  actor oid;
  campaign ctcc_devval_custody.campaigns%ROWTYPE;
  ack ctcc_devval_custody.acknowledgements%ROWTYPE;
  claimed_at timestamptz;
BEGIN
  actor := ctcc_devval_custody._require_collector();
  PERFORM ctcc_devval_custody._durable(true);
  IF p_campaign_sha256 IS NULL
    OR p_campaign_sha256 !~ '^[0-9a-f]{64}$'
    OR p_root_binding_sha256 IS NULL
    OR p_root_binding_sha256 !~ '^[0-9a-f]{64}$'
  THEN
    RAISE EXCEPTION 'ctcc_devval_dispatch_pin_denied';
  END IF;
  -- Shared with N142 publication and ACK: no wait for another write.
  IF pg_catalog.pg_try_advisory_xact_lock(421962,142001)
    IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'ctcc_devval_dispatch_busy';
  END IF;
  SELECT c.* INTO campaign
    FROM ctcc_devval_custody.campaigns c
    JOIN ctcc_devval_custody.acknowledgements a
      ON a.campaign_sha256=c.campaign_sha256
    WHERE c.campaign_sha256=p_campaign_sha256
    FOR KEY SHARE OF c,a NOWAIT;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'ctcc_devval_dispatch_ack_missing';
  END IF;
  SELECT a.* INTO ack FROM ctcc_devval_custody.acknowledgements a
    WHERE a.campaign_sha256=p_campaign_sha256
    FOR KEY SHARE NOWAIT;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'ctcc_devval_dispatch_ack_missing';
  END IF;
  claimed_at := pg_catalog.clock_timestamp();
  IF actor IN (campaign.publisher_role_oid,ack.observer_role_oid)
    OR campaign.publisher_role_oid=ack.observer_role_oid
    OR campaign.publication_xid=ack.acknowledgement_xid
    OR ack.acknowledged_at < campaign.recorded_at
    OR claimed_at < ack.acknowledged_at
    OR claimed_at >= campaign.development_start_at
  THEN
    RAISE EXCEPTION 'ctcc_devval_dispatch_ownership_or_time_denied';
  END IF;
  INSERT INTO ctcc_devval_custody.dispatch_claims (
    campaign_sha256,root_binding_sha256,collector_role_oid,dispatch_xid,
    claimed_at
  ) VALUES (
    p_campaign_sha256,p_root_binding_sha256,actor,
    pg_catalog.pg_current_xact_id()::text,claimed_at
  );
END
$ctcc_dispatch_claim$;

CREATE FUNCTION ctcc_devval_custody.read_dispatch_claim(
  p_campaign_sha256 text
)
RETURNS TABLE (
  campaign_sha256 text,
  root_binding_sha256 text,
  collector_role_oid oid,
  dispatch_xid text,
  claimed_at timestamptz,
  database_read_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $ctcc_dispatch_read$
DECLARE
  actor oid;
  claimed_actor oid;
BEGIN
  actor := ctcc_devval_custody._require_collector();
  PERFORM ctcc_devval_custody._durable(false);
  IF p_campaign_sha256 IS NULL OR p_campaign_sha256 !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'ctcc_devval_dispatch_read_pin_denied';
  END IF;
  SELECT d.collector_role_oid INTO claimed_actor
    FROM ctcc_devval_custody.dispatch_claims d
    WHERE d.campaign_sha256=p_campaign_sha256;
  IF FOUND AND claimed_actor<>actor THEN
    RAISE EXCEPTION 'ctcc_devval_dispatch_collector_changed';
  END IF;
  RETURN QUERY
    SELECT d.campaign_sha256::text,d.root_binding_sha256::text,
      d.collector_role_oid,d.dispatch_xid::text,d.claimed_at,
      pg_catalog.clock_timestamp()
    FROM ctcc_devval_custody.dispatch_claims d
    WHERE d.campaign_sha256=p_campaign_sha256
      AND d.collector_role_oid=actor;
END
$ctcc_dispatch_read$;

REVOKE ALL ON FUNCTION ctcc_devval_custody._require_collector() FROM PUBLIC;
REVOKE ALL ON FUNCTION ctcc_devval_custody.claim_dispatch(text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION ctcc_devval_custody.read_dispatch_claim(text) FROM PUBLIC;

-- ALTER DEFAULT PRIVILEGES by the installer could otherwise introduce a
-- direct table INSERT or API EXECUTE bypass. Roll the entire install back.
DO $ctcc_dispatch_acl$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_catalog.pg_class c,
      LATERAL pg_catalog.aclexplode(
        COALESCE(c.relacl,pg_catalog.acldefault('r',c.relowner))
      ) a
    WHERE c.oid='ctcc_devval_custody.dispatch_claims'::pg_catalog.regclass
      AND a.grantee<>c.relowner
  ) OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_proc p,
      LATERAL pg_catalog.aclexplode(
        COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))
      ) a
    WHERE p.oid IN (
      'ctcc_devval_custody._require_collector()'::pg_catalog.regprocedure,
      'ctcc_devval_custody.claim_dispatch(text,text)'::pg_catalog.regprocedure,
      'ctcc_devval_custody.read_dispatch_claim(text)'::pg_catalog.regprocedure
    ) AND a.grantee<>p.proowner
  ) THEN
    RAISE EXCEPTION 'ctcc_devval_dispatch_install_acl_denied';
  END IF;
END
$ctcc_dispatch_acl$;

COMMIT;
