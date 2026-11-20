"""Small source-owned field/MRO fixture, with no IO or execution authority."""

from pydantic import BaseModel


class FieldParent(BaseModel):
    pass


class AlternateFieldParent(FieldParent):
    pass


class FieldModel(FieldParent):
    value: int = 1


def entry():
    return 7
