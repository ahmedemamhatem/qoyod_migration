"""
Qoyod invoices -> ERPNext Sales Invoices
========================================

Imports Qoyod invoices (qoyod_data/invoices.json) as ERPNext Sales Invoices.

Resolution:
    contact_id        -> Customer.custom_qoyod_id
    line.product_id   -> Item.custom_qoyod_id
    tax               -> a manual VAT row against config.get_vat_account, at the
                         line's own tax_percent (config default otherwise)
    posting_date      <- issue_date  (due_date kept)

Pricing: Qoyod line carries is_inclusive. ERPNext handles this by marking the
tax row "included_in_print_rate", set per-invoice when its lines are inclusive.
(All lines within an invoice share is_inclusive in practice.)

Idempotent: Sales Invoice.custom_qoyod_id. Submitted (docstatus=1) so GL posts.
DRY RUN by default.
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
    """The tax % to apply. Uses the first taxed line's own tax_percent (so a
    dataset with a rate other than the default still imports correctly), else
    the configured default rate."""
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

    # Output-VAT account resolved from Settings / a Tax leaf account. The tax row
    # is built manually against this (not via a Taxes template) so the migration
    # is not coupled to any particular template's account head.
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
    print(f"  VAT account: {vat_account or '(none — posting without VAT)'}")
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
            any_tax = any(_f(li.get("tax_percent")) > 0 for li in lines)
            tax_rate = _line_tax_rate(lines, default_rate)

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

            # Manual VAT row against the resolved output-VAT account (only when a
            # line actually carries tax; rate taken from the data, not hardcoded).
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
