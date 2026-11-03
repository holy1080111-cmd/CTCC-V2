"""Uninstalled additive55 close-intent once index; no source/write permit.

The exact complete55 catalog/new native seal must independently verify the
actual index predicate/owner/shape. Source baseline None never admits it.
"""

from hashlib import sha256

INDEX_SQL = "CREATE UNIQUE INDEX qualification_native_close_intent_once_v55 ON public.qualification_submission_outcomes (reservation_id) WHERE (capture_json::jsonb ->> 'version') = 'ctcc.native_controlled_close_intent.v1'"
INDEX_SQL_SHA256 = "64ff1f91c80081501d5b476d8521fac13476bb8549e1adcb3dd5b56dc1dea73c"
INDEX_SPEC_JSON = '{"access_method":"btree","column":"reservation_id","index_name":"qualification_native_close_intent_once_v55","predicate":"(((capture_json)::jsonb ->> \'version\'::text) = \'ctcc.native_controlled_close_intent.v1\'::text)","table":"qualification_submission_outcomes","unique":true}'
INDEX_SPEC_SHA256 = "328a542ce818f0a9d245d8efa580e6c63801103d7e094740149f6c668ccc8568"


def statements():
    if sha256(INDEX_SQL.encode("ascii")).hexdigest() != INDEX_SQL_SHA256:
        raise ValueError("native_close_intent55_index_source_changed")
    if sha256(INDEX_SPEC_JSON.encode("ascii")).hexdigest() != INDEX_SPEC_SHA256:
        raise ValueError("native_close_intent55_index_source_changed")
    return (INDEX_SQL,)


def restore_statements():
    # The sole55 migration must first retain its inherited nonempty/native
    # outcome/tombstone downgrade refusal. No existing close proof is erased.
    statements()
    return ("DROP INDEX public.qualification_native_close_intent_once_v55",)
