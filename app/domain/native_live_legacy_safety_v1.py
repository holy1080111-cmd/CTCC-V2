"""Shared negative-only legacy Live fence. No identity or empty fallback."""

from sqlalchemy import text

TABLES = (
    "okx_live_execution_intents",
    "okx_live_position_state",
    "okx_live_order_state",
    "okx_live_algo_order_state",
    "okx_live_sync_checkpoints",
)
LOCK_ID = 1680010  # Original legacy Live execution_lock session key.
CLEAR_PREDICATE_SQL = """
 NOT EXISTS(SELECT 1 FROM public.okx_live_execution_intents WHERE (
  (status IN ('confirmed','rejected') AND operator_reconciled_at IS NULL AND operator_resolution_code IS NULL) OR
  (status IN ('reserved','acknowledged','ambiguous') AND operator_reconciled_at IS NOT NULL
   AND operator_reconciled_at<=clock_timestamp() AND operator_resolution_code='operator_confirmed_flat_exchange_state')
 ) IS NOT TRUE)
 AND NOT EXISTS(SELECT 1 FROM public.okx_live_position_state
  WHERE size IS DISTINCT FROM 0 OR available_size IS DISTINCT FROM 0 OR unrealized_pnl IS DISTINCT FROM 0)
 AND NOT EXISTS(SELECT 1 FROM public.okx_live_order_state
  WHERE (state IN ('filled','canceled','mmp_canceled')) IS NOT TRUE)
 AND NOT EXISTS(SELECT 1 FROM public.okx_live_algo_order_state
  WHERE (state IN ('effective','canceled','order_failed')) IS NOT TRUE)
 AND NOT EXISTS(SELECT 1 FROM public.okx_live_sync_checkpoints
  WHERE id<>1 OR status IS DISTINCT FROM 'reconciled' OR safety_latched IS DISTINCT FROM false
   OR safety_latch_code IS NOT NULL OR safety_latched_at IS NOT NULL OR safety_latch_version<0
   OR last_error IS NOT NULL OR reconciled_at IS NULL OR reconciled_at>clock_timestamp())
"""
CLEAR_SQL = text("SELECT " + CLEAR_PREDICATE_SQL)


async def require_legacy_live_negative_fence_v1(session):
    """Retain original global exclusion through the caller's transaction."""
    if (
        await session.scalar(text("SELECT pg_try_advisory_xact_lock(1680010)"))
        is not True
    ):
        raise ValueError("native_legacy_live_execution_inflight") from None
    if await session.scalar(CLEAR_SQL) is not True:
        raise ValueError("native_legacy_live_uncertain_or_tracked_exposure") from None
