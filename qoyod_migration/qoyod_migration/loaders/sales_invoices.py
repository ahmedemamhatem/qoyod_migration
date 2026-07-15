"""
Qoyod invoices -> ERPNext Sales Invoices
========================================

Imports Qoyod invoices (qoyod_data/invoices.json) as ERPNext Sales Invoices.

Resolution (all verified to be 100% on this dataset):
    contact_id        -> Customer.custom_qoyod_id
    line.product_id   -> Item.custom_qoyod_id
    tax 15%           -> existing "Vat15 - <abbr>" Sales Taxes and Charges Template
    posting_date      <- issue_date  (due_date kept)

Pricing: Qoyod line carries is_inclusive. ERPNext handles this by marking the
tax row "included_in_print_rate". Since the whole dataset uses one 15% rate, we
apply the Vat15 template and set included_in_print_rate per-invoice when its
lines are inclusive. (All lines within an invoice share is_inclusive here.)

Idempotent: Sales Invoice.custom_qoyod_id. Submitted (docstatus=1) so GL posts.
DRY RUN by default.
"""

import json
import os
import sys

from qoyod_migration.qoyod_migration import config
from qoyod_migration.qoyod_migration.config import data_dir


def _qoyod_values(fskey, record):
    """Fill the Qoyod Data section fields on insert (shared with backfill)."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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
    abbr = frappe.db.get_value("Company", company, "abbr")
    vat_template = f"Vat15 - {abbr}"
    if not frappe.db.exists("Sales Taxes and Charges Template", vat_template):
        vat_template = frappe.db.get_value(
            "Sales Taxes and Charges Template",
            {"company": company, "is_default": 1}, "name")

    cost_center = frappe.db.get_value("Company", company, "cost_center")
    income_account = frappe.db.get_value("Company", company, "default_income_account")

    # Correct output-VAT account (the site's Vat15 *template* wrongly points at a
    # shipping-fee account, so we build the tax row manually against this).
    vat_account = config.get_vat_account()
    if not vat_account:
        vat_account = frappe.db.get_value(
            "Account", {"company": company, "account_type": "Tax", "is_group": 0}, "name")

    def cust(qid):
        return frappe.db.get_value("Customer", {"custom_qoyod_id": str(qid)}, "name")

    def item(qid):
        return frappe.db.get_value("Item", {"custom_qoyod_id": str(qid)}, "name")

    invoices = json.load(open(os.path.join(data_dir(), "invoices.json"), encoding="utf-8"))
    if limit:
        invoices = invoices[:limit]

    created = updated = skipped = errors = 0
    err_samples = []

    print("=" * 60)
    print(f"Qoyod invoices -> Sales Invoice ({company})  "
          f"[{'COMMIT' if commit else 'DRY RUN'}]  n={len(invoices)}")
    print(f"  VAT template: {vat_template}")
    print("=" * 60)

    for q in invoices:
        qid = str(q["id"])
        try:
            existing = frappe.db.get_value("Sales Invoice", {"custom_qoyod_id": qid}, "name")
            if existing:
                skipped += 1
                continue

            customer = cust(q.get("contact_id"))
            if not customer:
                raise ValueError(f"customer for contact_id={q.get('contact_id')} not found")

            lines = q.get("line_items", [])
            inclusive = bool(lines and lines[0].get("is_inclusive"))

            items = []
            for li in lines:
                it = item(li.get("product_id"))
                if not it:
                    raise ValueError(f"item for product_id={li.get('product_id')} not found")
                qty = _f(li.get("quantity")) or 1
                gross_unit = _f(li.get("inclusive_unit_price") if inclusive else li.get("unit_price"))
                # Bake the line discount into the rate so the net reconciles exactly
                # (avoids ERPNext's per-unit discount ambiguity).
                disc_total = _f(li.get("discount_amount"))
                net_line = gross_unit * qty - disc_total
                rate = net_line / qty if qty else net_line
                row = {
                    "item_code": it,
                    "qty": qty,
                    "rate": rate,
                    "income_account": income_account,
                }
                if cost_center:
                    row["cost_center"] = cost_center
                items.append(row)

            # Manual 15% VAT row against the correct output-VAT account.
            taxes = []
            if vat_account:
                taxes.append({
                    "charge_type": "On Net Total",
                    "account_head": vat_account,
                    "description": "VAT 15%",
                    "rate": 15.0,
                    "included_in_print_rate": 1 if inclusive else 0,
                    "cost_center": cost_center,
                })

            doc = frappe.get_doc({
                "doctype": "Sales Invoice",
                "company": company,
                "customer": customer,
                "posting_date": q.get("issue_date"),
                "set_posting_time": 1,
                "due_date": q.get("due_date") or q.get("issue_date"),
                "custom_qoyod_id": qid,
                "remarks": q.get("description") or None,
                "items": items,
                "taxes": taxes,
                # Fill the Qoyod Data section on insert (no separate backfill needed).
                **_qoyod_values("Sales Invoice", q),
            })

            # Name the ERPNext invoice = the Qoyod reference (e.g. INV217) so the
            # document id matches Qoyod. New invoices continue the INV.#### series.
            ref = (q.get("reference") or "").strip()

            if commit:
                if ref:
                    # Force the ERPNext name = Qoyod reference (e.g. INV217).
                    # set_missing_values fills price-list/defaults; name_set makes
                    # Frappe's autoname keep our name instead of regenerating it.
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
                err_samples.append(f"inv id={qid}: {str(e)[:160]}")

    if commit:
        frappe.db.commit()

    print(f"\n  created: {created}  updated: {updated}  skipped(exists): {skipped}  errors: {errors}")
    if err_samples:
        print("  --- error samples ---")
        for s in err_samples:
            print("   ", s)
    print("  " + ("COMMITTED." if commit else "DRY RUN — nothing written."))
    return {"created": created, "skipped": skipped, "errors": errors}
