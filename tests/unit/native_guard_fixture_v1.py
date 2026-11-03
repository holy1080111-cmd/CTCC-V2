"""Tiny source-compiled fixture; no network, DB, files or privileged operation."""

import asyncio

closed = []


def leaf(value=7):
    return value


def entry():
    return leaf()


async def async_entry():
    try:
        await asyncio.sleep(0)
        return leaf()
    finally:
        closed.append("closed")
