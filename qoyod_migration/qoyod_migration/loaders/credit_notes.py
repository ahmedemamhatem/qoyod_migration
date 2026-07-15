"""
Qoyod credit_notes -> ERPNext Sales Invoices (is_return=1)
=========================================================

A Qoyod credit note reverses an invoice. In ERPNext this is a Sales Invoice
with is_return=1, negative quantities, and return_against pointing at the
original invoice.

  parent_id  -> return_against (the imported Sales Invoice with that qoyod_id)
  contact_id -> Customer
  line.price -> rate ; qty negated
  tax        -> the resolved VAT account (config.get_vat_account)

Idempotent (custom_qoyod_id, prefixed 'CN-'). Submitted. DRY RUN by default.
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


def _line_tax_rate(lines, default_rate):
    for li in lines:
        r = _f(li.get("tax_percent"))
        if r > 0:
            return r
    return default_rate


def build(commit=False, limit=None):
    import frappe

    company = config.get_company()
    currency = config.get_currency()
    default_rate = config.get_vat_rate()
    cost_center = frappe.db.get_value("Company", company, "cost_center")
    income_account = frappe.db.get_value("Company", company, "default_income_account")
    vat_account = config.get_vat_account()

    def cust(qid):
        return frappe.db.get_value("Customer", {"custom_qoyod_id": str(qid)}, "name")

    def item(qid):
        return frappe.db.get_value("Item", {"custom_qoyod_id": str(qid)}, "name")

    def parent_si(qid):
        return frappe.db.get_value("Sales Invoice", {"custom_qoyod_id": str(qid)}, "name")

    notes = json.load(open(os.path.join(data_dir(), "credit_notes.json"), encoding="utf-8"))
    if limit:
        notes = notes[:limit]

    created = skipped = errors = 0
    err_samples = []
    print("=" * 60)
    print(f"Qoyod credit_notes -> Sales Invoice return  "
          f"[{'COMMIT' if commit else 'DRY RUN'}]  n={len(notes)}")
    print("=" * 60)

    for q in notes:
        qid = str(q["id"])
        try:
            if frappe.db.get_value("Sales Invoice", {"custom_qoyod_id": f"CN-{qid}"}, "name"):
                skipped += 1
                continue

            customer = cust(q.get("contact_id"))
            if not customer:
                raise ValueError(f"customer contact_id={q.get('contact_id')} not found")

            return_against = parent_si(q.get("parent_id")) if q.get("parent_id") else None
            lines = q.get("line_items", [])
            inclusive = bool(lines and lines[0].get("is_inclusive"))
            any_tax = any(_f(li.get("tax_percent")) > 0 for li in lines)
            tax_rate = _line_tax_rate(lines, default_rate)

            items = []
            for li in lines:
                it = item(li.get("product_id"))
                if not it:
                    raise ValueError(f"item product_id={li.get('product_id')} not found")
                qty = _f(li.get("quantity")) or 1
                gross_unit = _f(li.get("inclusive_unit_price") if inclusive else li.get("price"))
                disc_total = _f(li.get("discount_amount"))
                net_line = gross_unit * qty - disc_total
                rate = net_line / qty if qty else net_line
                row = {
                    "item_code": it,
                    "qty": -qty,          # return => negative
                    "rate": rate,
                    "income_account": income_account,
                }
                if cost_center:
                    row["cost_center"] = cost_center
                items.append(row)

            taxes = []
            if vat_account and any_tax:
                taxes.append({
                    "charge_type": "On Net Total",
                    "account_head": vat_account,
                    "description": f"VAT {tax_rate:g}%",
                    "rate": tax_rate,
                    "included_in_print_rate": 1 if inclusive else 0,
                    "cost_center": cost_center,
                })

            doc = frappe.get_doc({
                "doctype": "Sales Invoice",
                "company": company,
                "customer": customer,
                "currency": currency,
                "is_return": 1,
                "return_against": return_against,
                "posting_date": q.get("issue_date"),
                "set_posting_time": 1,
                "custom_qoyod_id": f"CN-{qid}",
                "remarks": q.get("notes") or q.get("issuance_reason") or None,
                "items": items,
                "taxes": taxes,
                # Fill the Qoyod Data section on insert (credit notes share the SI fieldset).
                **_qoyod_values("Sales Invoice", q),
            })

            # Name the ERPNext return = the Qoyod credit-note reference (e.g. CRN18).
            ref = (q.get("reference") or "").strip()

            if commit:
                if ref:
                    doc.set_missing_values()
                    doc.name = ref
                    doc.flags.name_set = True
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
                err_samples.append(f"cn id={qid}: {str(e)[:180]}")

    if commit:
        frappe.db.commit()
    print(f"\n  created: {created}  skipped: {skipped}  errors: {errors}")
    for s in err_samples:
        print("   ", s)
    print("  " + ("COMMITTED." if commit else "DRY RUN — nothing written."))
    return {"created": created, "skipped": skipped, "errors": errors}
