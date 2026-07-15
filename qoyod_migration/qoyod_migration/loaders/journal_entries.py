"""
Qoyod journal_entries -> ERPNext Journal Entries
================================================

Each Qoyod JE has debit_amounts[] and credit_amounts[], every line referencing
account_id (Qoyod). We resolve account_id -> Account.custom_qoyod_id (the
-Qoyod accounts imported earlier). All 1680 verified balanced, all 5000 lines
resolvable.

Idempotent (Journal Entry.custom_qoyod_id). Submitted. DRY RUN by default.
"""

import json
import os

from qoyod_migration.qoyod_migration import config
from qoyod_migration.qoyod_migration.config import data_dir


def _qoyod_values(fskey, record):
	"""Fill the Qoyod Data section fields on insert (shared with backfill)."""
	try:
		from qoyod_migration.qoyod_migration.custom_fields import txn_fields as tf
		return tf.qoyod_values(fskey, record)
	except Exception:  # noqa: BLE001
		return {}



def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def build(commit=False, limit=None):
    import frappe

    company = config.get_company()
    cost_center = frappe.db.get_value("Company", company, "cost_center")

    acc_cache = {}

    def acc(qid):
        if qid not in acc_cache:
            acc_cache[qid] = frappe.db.get_value("Account", {"custom_qoyod_id": str(qid)}, "name")
        return acc_cache[qid]

    entries = json.load(open(os.path.join(data_dir(), "journal_entries.json"), encoding="utf-8"))
    if limit:
        entries = entries[:limit]

    created = skipped = errors = 0
    err_samples = []
    print("=" * 60)
    print(f"Qoyod journal_entries -> Journal Entry  "
          f"[{'COMMIT' if commit else 'DRY RUN'}]  n={len(entries)}")
    print("=" * 60)

    for q in entries:
        qid = str(q["id"])
        try:
            if frappe.db.get_value("Journal Entry", {"custom_qoyod_id": qid}, "name"):
                skipped += 1
                continue

            accounts = []
            for x in q.get("debit_amounts", []):
                a = acc(x.get("account_id"))
                if not a:
                    raise ValueError(f"account_id={x.get('account_id')} not found")
                accounts.append({
                    "account": a,
                    "debit_in_account_currency": _f(x.get("amount")),
                    "credit_in_account_currency": 0,
                    "cost_center": cost_center,
                })
            for x in q.get("credit_amounts", []):
                a = acc(x.get("account_id"))
                if not a:
                    raise ValueError(f"account_id={x.get('account_id')} not found")
                accounts.append({
                    "account": a,
                    "debit_in_account_currency": 0,
                    "credit_in_account_currency": _f(x.get("amount")),
                    "cost_center": cost_center,
                })

            doc = frappe.get_doc({
                "doctype": "Journal Entry",
                "company": company,
                "posting_date": q.get("date"),
                "custom_qoyod_id": qid,
                "user_remark": q.get("description") or None,
                "accounts": accounts,
                # Fill the Qoyod Data section on insert.
                **_qoyod_values("Journal Entry", q),
            })

            if commit:
                doc.insert(ignore_permissions=True)
                doc.submit()
                created += 1
            else:
                doc.set_missing_values()
                created += 1

        except Exception as e:  # noqa: BLE001
            errors += 1
            if len(err_samples) < 15:
                err_samples.append(f"je id={qid}: {str(e)[:180]}")

    if commit:
        frappe.db.commit()
    print(f"\n  created: {created}  skipped: {skipped}  errors: {errors}")
    for s in err_samples:
        print("   ", s)
    print("  " + ("COMMITTED." if commit else "DRY RUN — nothing written."))
    return {"created": created, "skipped": skipped, "errors": errors}
