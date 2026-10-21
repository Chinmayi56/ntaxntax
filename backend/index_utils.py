"""Idempotent MongoDB index initialization helpers for NTAXCO.

Indexes are never dropped or renamed here. If an equivalent index already exists
under a different name, it is reused. If the same key pattern exists with
incompatible options, the helper reports it and leaves the existing index alone.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

logger = logging.getLogger(__name__)


def _keys(value: Any) -> tuple[tuple[str, int], ...]:
    if isinstance(value, str):
        return ((value, 1),)
    if isinstance(value, dict):
        return tuple((str(k), int(v)) for k, v in value.items())
    return tuple((str(k), int(v)) for k, v in value)


def _option(index: dict, name: str, default: Any = None) -> Any:
    return index.get(name, default)


def _compatible(index: dict, keys: tuple[tuple[str, int], ...], unique: bool,
                sparse: bool, partial_filter: dict | None) -> bool:
    return (
        _keys(index.get("key", {})) == keys
        and bool(_option(index, "unique", False)) == bool(unique)
        and bool(_option(index, "sparse", False)) == bool(sparse)
        and _option(index, "partialFilterExpression") == partial_filter
    )


async def ensure_index(collection, keys: Any, *, unique: bool = False,
                       sparse: bool = False, name: str | None = None,
                       partial_filter: dict | None = None,
                       logger_: logging.Logger | None = None) -> bool:
    """Ensure an index exists without creating equivalent duplicate indexes.

    Returns True when a compatible index already existed or was created.
    Returns False when an incompatible same-key index exists or a unique index
    cannot safely be created because duplicate/invalid values are present.
    """
    log = logger_ or logger
    normalized = _keys(keys)

    existing = await collection.list_indexes().to_list(length=None)
    for index in existing:
        if _compatible(index, normalized, unique, sparse, partial_filter):
            log.info(
                "Reusing compatible MongoDB index %s on %s",
                index.get("name"), getattr(collection, "name", "<collection>"),
            )
            return True

    same_keys = [idx for idx in existing if _keys(idx.get("key", {})) == normalized]
    if same_keys:
        log.warning(
            "MongoDB index conflict on %s for keys %s: existing index(es) "
            "have incompatible options: %s. No existing index will be "
            "dropped or renamed.",
            getattr(collection, "name", "<collection>"),
            normalized,
            [idx.get("name") for idx in same_keys],
        )
        return False

    if unique and partial_filter is None:
        # The BillBook plan migration is a full unique index on `id`. Check
        # data before asking MongoDB to enforce uniqueness so legacy bad data
        # is reported clearly and startup never needs to delete/repair rows.
        invalid: list[Any] = []
        counts: dict[Any, int] = {}
        cursor = collection.find({}, {"id": 1, "_id": 0})
        async for doc in cursor:
            value = doc.get("id")
            if value is None or value == "":
                invalid.append(value)
            else:
                try:
                    counts[value] = counts.get(value, 0) + 1
                except TypeError:
                    invalid.append(value)
        duplicates = [value for value, count in counts.items() if count > 1]
        if invalid or duplicates:
            log.error(
                "Cannot create unique MongoDB index %s on %s: invalid/missing "
                "id values=%d, duplicate id values=%d. Data was not modified "
                "and no index was dropped or renamed.",
                name or "<unnamed>",
                getattr(collection, "name", "<collection>"),
                len(invalid), len(duplicates),
            )
            return False

    kwargs = {"unique": unique, "sparse": sparse}
    if name:
        kwargs["name"] = name
    if partial_filter is not None:
        kwargs["partialFilterExpression"] = partial_filter
    try:
        await collection.create_index(keys, **kwargs)
    except Exception as exc:
        log.error(
            "MongoDB index creation failed for %s on %s: %s. "
            "No existing index was dropped or renamed.",
            name or "<unnamed>", getattr(collection, "name", "<collection>"), exc,
        )
        return False
    log.info("Created MongoDB index %s on %s", name or "<unnamed>",
             getattr(collection, "name", "<collection>"))
    return True
