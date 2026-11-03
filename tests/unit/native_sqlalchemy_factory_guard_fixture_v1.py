"""Ordinary source for resume checks, outside pytest assertion rewriting."""

import asyncio

from sqlalchemy import (
    create_engine as create_engine,  # noqa: PLC0414 -- Preserve the exported SQLAlchemy factory used in the integrity tests.
)


async def _guarded_factory_fixture():
    await asyncio.sleep(0)
    return "reached"
