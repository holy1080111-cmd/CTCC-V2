"""Exact additive current55 configuration interlock; no new order authority.

The existing Control/ledger/submission guards stay byte-for-byte unchanged.
This BEFORE guard serializes with the genuine configuration row issuer's same
UID -> Control -> scope locks and rejects unresolved configuration at positive
Arm/reserve/consume/initial-submission boundaries. Negative DATA and own close
transitions still reach their original independent guards.
"""

import hashlib

FUNCTION = "qualification_leverage_configuration_positive_guard_v55"
TRIGGER = "leverage_configuration_positive_guard_v55"
BODY_SHA256 = "3af959dfb16be4dfdd544db592a7a3fcd96452c71826ba25ee355688b7445681"
CURRENT_SQL = r"""CREATE FUNCTION public.qualification_leverage_configuration_positive_guard_v55() RETURNS trigger AS $ctcc$
DECLARE selected_environment text; selected_account text; positive_action boolean; lock_key bigint;
BEGIN
 IF TG_TABLE_SCHEMA<>'public' THEN RAISE EXCEPTION 'leverage_configuration_positive_target_denied'; END IF;
 IF TG_TABLE_NAME='demo_control_journal' AND TG_OP='INSERT' THEN
  positive_action := NEW.action='arm_requested';
  selected_environment := NEW.environment; selected_account := NEW.account_id;
 ELSIF TG_TABLE_NAME='qualification_reservations' AND TG_OP IN ('INSERT','UPDATE') THEN
  positive_action := true;
  IF TG_OP='UPDATE' THEN positive_action := OLD.state='reserved' AND NEW.state='consumed'; END IF;
  selected_environment := NEW.environment; selected_account := NEW.account_id;
 ELSIF TG_TABLE_NAME='qualification_reservation_transitions' AND TG_OP='INSERT' THEN
  positive_action := NEW.to_state IN ('reserved','consumed');
  SELECT environment,account_id INTO selected_environment,selected_account
   FROM public.qualification_reservations WHERE reservation_id=NEW.reservation_id;
 ELSIF TG_TABLE_NAME='qualification_submission_outcomes' AND TG_OP='INSERT' THEN
  positive_action := NEW.observation_kind='initial';
  SELECT environment,account_id INTO selected_environment,selected_account
   FROM public.qualification_reservations WHERE reservation_id=NEW.reservation_id;
 ELSE RAISE EXCEPTION 'leverage_configuration_positive_target_denied';
 END IF;
 IF positive_action IS NOT TRUE THEN RETURN NEW; END IF;
 IF selected_environment IS NULL OR selected_account IS NULL THEN
  RAISE EXCEPTION 'leverage_configuration_positive_scope_denied';
 END IF;
 lock_key := ('x'||substr(encode(sha256(convert_to('ctcc-qualification-account-v1','UTF8')
  ||decode('00','hex')||convert_to(selected_environment,'UTF8')||decode('00','hex')
  ||convert_to(selected_account,'UTF8')),'hex'),1,16))::bit(64)::bigint;
 PERFORM pg_advisory_xact_lock(lock_key);
 PERFORM 1 FROM public.demo_account_controls
  WHERE environment=selected_environment AND account_id=selected_account FOR UPDATE;
 PERFORM 1 FROM public.qualification_account_scopes
  WHERE environment=selected_environment AND account_id=selected_account
  ORDER BY settlement_currency FOR UPDATE;
 IF public.qualification_leverage_configuration_blocked_v55(selected_environment,selected_account) IS DISTINCT FROM false THEN
  RAISE EXCEPTION 'leverage_configuration_unresolved_blocks_positive_action';
 END IF;
 RETURN NEW;
END; $ctcc$ LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp;"""
TRIGGERS = {
    "demo_control_journal": (7, "BEFORE INSERT"),
    "qualification_reservations": (23, "BEFORE INSERT OR UPDATE"),
    "qualification_reservation_transitions": (7, "BEFORE INSERT"),
    "qualification_submission_outcomes": (7, "BEFORE INSERT"),
}


def statements():
    body = CURRENT_SQL.split("$ctcc$")
    if len(body) != 3 or hashlib.sha256(body[1].encode()).hexdigest() != BODY_SHA256:
        raise ValueError("leverage_configuration_positive_guard_source_changed")
    return (
        CURRENT_SQL,
        *(
            "CREATE TRIGGER "
            + TRIGGER
            + " "
            + timing
            + " ON public."
            + table
            + " FOR EACH ROW EXECUTE FUNCTION public."
            + FUNCTION
            + "()"
            for table, (_, timing) in TRIGGERS.items()
        ),
        "REVOKE ALL ON FUNCTION public." + FUNCTION + "() FROM PUBLIC",
    )


def restore_statements():
    statements()
    return (
        *(
            "DROP TRIGGER " + TRIGGER + " ON public." + table
            for table in reversed(TRIGGERS)
        ),
        "DROP FUNCTION public." + FUNCTION + "()",
    )
