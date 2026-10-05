"""PATCH helper: fields sent as null clear nullable columns; null on a NOT NULL column is ignored."""
from pydantic import BaseModel


def apply_patch(obj, body: BaseModel, nullable: frozenset[str] = frozenset(), skip: frozenset[str] = frozenset()) -> dict:
    data = body.model_dump(exclude_unset=True)
    applied = {}
    for field, value in data.items():
        if field in skip:
            continue
        if value is None and field not in nullable:
            continue
        setattr(obj, field, value)
        applied[field] = value
    return applied
