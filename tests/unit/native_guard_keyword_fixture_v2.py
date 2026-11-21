"""Normally imported source fixture for exact keyword-default pinning."""


def leaf(*, first=None, second=None):
    return first, second


def entry(*, value=7):
    return value
