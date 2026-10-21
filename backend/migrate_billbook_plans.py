#!/usr/bin/env python3
"""Inspect and (optionally) canonicalise legacy ``billbook_plans`` records.

    python migrate_billbook_plans.py                    # DRY RUN: show every record + what would change
    python migrate_billbook_plans.py --apply            # write the additive changes (after a JSON backup)
    python migrate_billbook_plans.py --report out.json  # also save the full report

Safety rules (enforced by ``billbook_plans.plan_migration``):
  * Never deletes records, subscriptions, payments or invoices; never removes legacy fields.
  * Only ADDS / re-types canonical fields whose value the same record already carries
    (legacy price alias, duration text, explicit legacy status). Never invents a price.
  * Never activates, publishes or un-archives anything that was not already stored that way.
  * Never writes a zero duration.
  * Idempotent: running it again after ``--apply`` changes nothing.
  * ``--accept-name-durations`` additionally lets a duration be read from a plan *name* such as
    "6 Months" - off by default because a name is a label, not configuration.
Records that still lack a valid price/duration are listed under ``needs_admin_action``; fix them in
Admin -> My BillBook -> Subscription Plans.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from billbook_plans import normalize_plan, plan_migration, doc_id  # noqa: E402
from billbook_rules import BILLBOOK_FEATURES, PLAN_CATALOG  # noqa: E402


def _connect():
    try:
        from dotenv import load_dotenv
        load_dotenv(HERE / ".env")
    except ImportError:  # python-dotenv is optional here
        pass
    from pymongo import MongoClient

    url = os.getenv("MONGO_URL")
    if not url:
        raise SystemExit("MONGO_URL is not set (put it in backend/.env or the environment).")
    client = MongoClient(url, serverSelectionTimeoutMS=8000)
    return client[os.getenv("DB_NAME", "ntaxco")]


def _jsonable(value):
    try:
        from bson import json_util
        return json.loads(json_util.dumps(value))
    except ImportError:  # bson ships with pymongo; plain JSON is fine for the dry-run helpers/tests
        return json.loads(json.dumps(value, default=str))


def describe(docs):
    """Per-record view: stored field names/values and how the platform interprets them."""
    rows = []
    for doc in docs:
        pid = doc_id(doc)
        base = next((c for c in PLAN_CATALOG if c["id"] == pid), None)
        norm = normalize_plan(doc, base, {}, BILLBOOK_FEATURES, pid or None)
        rows.append({
            "id": pid or None,
            "stored_fields": {k: v for k, v in _jsonable(doc).items() if k != "_id"},
            "interpreted": {k: norm.get(k) for k in ("price", "currency", "duration_days", "duration_months",
                                                      "active", "published", "archived", "issues",
                                                      "price_field", "duration_source")},
        })
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the changes (default is a dry run)")
    ap.add_argument("--accept-name-durations", action="store_true", help="derive a missing duration from the plan name")
    ap.add_argument("--report", help="write the full JSON report to this path")
    ap.add_argument("--backup-dir", default=str(HERE), help="where the pre-change backup JSON is written")
    args = ap.parse_args(argv)

    db = _connect()
    docs = list(db["billbook_plans"].find({}))
    result = plan_migration(docs, PLAN_CATALOG, {}, BILLBOOK_FEATURES, accept_name_durations=args.accept_name_durations)
    report = {"mode": "apply" if args.apply else "dry-run", "records": describe(docs), **_jsonable(result)}

    if args.apply and result["updates"]:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = Path(args.backup_dir) / f"billbook_plans_backup_{stamp}.json"
        touched = {u["id"] for u in result["updates"]}
        backup.write_text(json.dumps(_jsonable([d for d in docs if doc_id(d) in touched]), indent=2), encoding="utf-8")
        print(f"Backup of records about to change: {backup}")
        now = datetime.now(timezone.utc).isoformat()
        for update in result["updates"]:
            outcome = db["billbook_plans"].update_one({"_id": update["_id"]}, {"$set": {**update["set"], "migrated_at": now}})
            update["matched"] = outcome.matched_count
        report["updates"] = _jsonable(result["updates"])

    print(f"Mode: {report['mode']}")
    print(f"Records inspected: {len(docs)} | to update: {len(result['updates'])} | unchanged: {len(result['unchanged'])}")
    for update in result["updates"]:
        print(f"  - {update['id']}: " + "; ".join(update["notes"]))
    if result["duplicates"]:
        print("Duplicate-looking plans (kept, but only the first is purchasable):")
        for item in result["duplicates"]:
            print(f"  - {item['id']} duplicates {item['duplicate_of']} -> archive or unpublish it in Admin if it is not needed")
    if result["needs_admin_action"]:
        print("STILL NEEDS ADMIN ACTION:")
        for item in result["needs_admin_action"]:
            print(f"  - {item['id']} ({item['name']}): {', '.join(item['problems'])}. {item['action']}")
    if result["unidentified"]:
        print(f"Records without a usable id (not touched): {len(result['unidentified'])}")
    if not args.apply and result["updates"]:
        print("\nDry run only. Re-run with --apply to write these additive changes.")
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Full report written to {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
