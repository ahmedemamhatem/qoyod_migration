"""
Qoyod bills -> ERPNext Purchase Invoices
========================================

Mirrors the (proven) Sales Invoice loader. Differences:
  * party comes from embedded `contact` dict -> Supplier.custom_qoyod_id
  * line tax field is `tax` (not tax_percent)
  * each line needs an expense_account (company default expense account)
  * input VAT posts to 2310 - VAT 15%

Idempotent (Purchase Invoice.custom_qoyod_id). Submitted. DRY RUN by default.
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
    expense_account = frappe.db.get_value("Company", company, "default_expense_account")
    vat_account = config.get_vat_account()

    def supp(qid):
        return frappe.db.get_value("Supplier", {"custom_qoyod_id": str(qid)}, "name")

    def item(qid):
        return frappe.db.get_value("Item", {"custom_qoyod_id": str(qid)}, "name")

    bills = json.load(open(os.path.join(data_dir(), "bills.json"), encoding="utf-8"))
    if limit:
        bills = bills[:limit]

    created = skipped = errors = 0
    err_samples = []

    print("=" * 60)
    print(f"Qoyod bills -> Purchase Invoice ({company})  "
          f"[{'COMMIT' if commit else 'DRY RUN'}]  n={len(bills)}")
    print("=" * 60)

    for q in bills:
        qid = str(q["id"])
        try:
            if frappe.db.get_value("Purchase Invoice", {"custom_qoyod_id": qid}, "name"):
                skipped += 1
                continue

            contact = q.get("contact") or {}
            supplier = supp(contact.get("id"))
            if not supplier:
                raise ValueError(f"supplier for contact id={contact.get('id')} not found")

            lines = q.get("line_items", [])
            inclusive = bool(lines and lines[0].get("is_inclusive"))
            # Some bill lines are VAT-exempt (tax=0). Only charge VAT if any line has tax.
            any_tax = any(_f(li.get("tax")) > 0 for li in lines)

            items = []
            for li in lines:
                it = item(li.get("product_id"))
                if not it:
                    raise ValueError(f"item for product_id={li.get('product_id')} not found")
                qty = _f(li.get("quantity")) or 1
                gross_unit = _f(li.get("inclusive_unit_price") if inclusive else li.get("unit_price"))
                disc_total = _f(li.get("discount_amount"))
                net_line = gross_unit * qty - disc_total
                rate = net_line / qty if qty else net_line
                row = {
                    "item_code": it,
                    "qty": qty,
                    "rate": rate,
                    "expense_account": expense_account,
                }
                if cost_center:
                    row["cost_center"] = cost_center
                items.append(row)

            taxes = []
            if vat_account and any_tax:
                taxes.append({
                    "charge_type": "On Net Total",
                    "account_head": vat_account,
                    "description": "VAT 15%",
                    "rate": 15.0,
                    "category": "Total",
                    "add_deduct_tax": "Add",
                    "included_in_print_rate": 1 if inclusive else 0,
                    "cost_center": cost_center,
                })

            doc = frappe.get_doc({
                "doctype": "Purchase Invoice",
                "company": company,
                "supplier": supplier,
                "posting_date": q.get("issue_date"),
                "set_posting_time": 1,
                "bill_no": q.get("reference"),
                "bill_date": q.get("issue_date"),
                "due_date": q.get("due_date") or q.get("issue_date"),
                "custom_qoyod_id": qid,
                "remarks": q.get("notes") or None,
                "items": items,
                "taxes": taxes,
                # Fill the Qoyod Data section on insert.
                **_qoyod_values("Purchase Invoice", q),
            })

            if commit:
                doc.insert(ignore_permissions=True)
                doc.submit()
                created += 1
            else:
                doc.set_missing_values()
                doc.run_method("calculate_taxes_and_totals")
                created += 1

        except Exception as e:  # noqa: BLE001
            errors += 1
            if len(err_samples) < 12:
                err_samples.append(f"bill id={qid}: {str(e)[:160]}")

    if commit:
        frappe.db.commit()

    print(f"\n  created: {created}  skipped(exists): {skipped}  errors: {errors}")
    if err_samples:
        print("  --- error samples ---")
        for s in err_samples:
            print("   ", s)
    print("  " + ("COMMITTED." if commit else "DRY RUN — nothing written."))
    return {"created": created, "skipped": skipped, "errors": errors}
