import asyncio
import logging
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from index_utils import ensure_index


class FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    def __aiter__(self):
        self._i = iter(self.docs)
        return self

    async def __anext__(self):
        try:
            return next(self._i)
        except StopIteration:
            raise StopAsyncIteration


class FakeIndexCursor:
    def __init__(self, indexes):
        self.indexes = indexes

    async def to_list(self, length=None):
        return list(self.indexes)


class FakeCollection:
    def __init__(self, indexes=None, docs=None):
        self.name = "billbook_plans"
        self.indexes = indexes or []
        self.docs = docs or []
        self.created = []

    def list_indexes(self):
        return FakeIndexCursor(self.indexes)

    def find(self, *_args, **_kwargs):
        return FakeCursor(self.docs)

    async def create_index(self, keys, **kwargs):
        self.created.append((keys, kwargs))
        self.indexes.append({
            "name": kwargs.get("name", "generated"),
            "key": dict(keys) if not isinstance(keys, str) else {keys: 1},
            "unique": kwargs.get("unique", False),
            "sparse": kwargs.get("sparse", False),
            "partialFilterExpression": kwargs.get("partialFilterExpression"),
        })
        return kwargs.get("name", "generated")


class IndexInitializationTests(unittest.TestCase):
    def test_existing_billbook_plan_unique_index_is_reused_even_with_legacy_name(self):
        collection = FakeCollection(indexes=[{
            "name": "bb_plan_id",
            "key": {"id": 1},
            "unique": True,
        }], docs=[{"id": "PLAN-1"}])

        result = asyncio.run(
            ensure_index(collection, "id", unique=True, name="uniq_billbook_plan_id")
        )

        self.assertTrue(result)
        self.assertEqual(collection.created, [])
        self.assertEqual(collection.indexes[0]["name"], "bb_plan_id")

    def test_unique_index_migration_reports_duplicate_plan_ids_without_writing(self):
        collection = FakeCollection(docs=[
            {"id": "PLAN-1"},
            {"id": "PLAN-1"},
        ])

        result = asyncio.run(
            ensure_index(collection, "id", unique=True, name="uniq_billbook_plan_id")
        )

        self.assertFalse(result)
        self.assertEqual(collection.created, [])

    def test_incompatible_same_key_index_is_not_dropped_or_recreated(self):
        collection = FakeCollection(indexes=[{
            "name": "bb_plan_id_nonunique",
            "key": {"id": 1},
            "unique": False,
        }])

        result = asyncio.run(
            ensure_index(collection, "id", unique=True, name="uniq_billbook_plan_id")
        )

        self.assertFalse(result)
        self.assertEqual(collection.created, [])
        self.assertEqual(collection.indexes[0]["name"], "bb_plan_id_nonunique")


if __name__ == "__main__":
    unittest.main()
