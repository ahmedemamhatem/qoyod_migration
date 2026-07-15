"""
Qoyod quotes -> ERPNext Quotation
=================================

Quote lines mirror invoice lines (product_id, unit_price, tax_percent, discounts,
is_inclusive). Same pricing rule as sales invoices: bake line discount into the
rate, add a manual VAT row (only when a line has tax) against the resolved VAT
account, at the line's own tax rate (config default otherwise).

Status map: Approved/Invoiced -> submitted; Cancelled -> cancelled draft.
Idempotent (Quotation.custom_qoyod_id). DRY RUN by default.
"""

import json
import os

from qoyod_migration.qoyod_migration import config
from qoyod_migration.qoyod_migration.config import data_dir


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


def ensure_field():
	import frappe
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
	create_custom_fields({
		"Quotation": [{
			"fieldname": "custom_qoyod_id", "label": "Qoyod ID", "fieldtype": "Data",
			"read_only": 1, "insert_after": "title",
		}],
	}, update=True)
	frappe.db.commit()


def build(commit=False, limit=None):
	import frappe

	if commit:
		ensure_field()

	company = config.get_company()
	currency = config.get_currency()
	default_rate = config.get_vat_rate()
	cost_center = frappe.db.get_value("Company", company, "cost_center")
	vat_account = config.get_vat_account()

	def cust(qid):
		return frappe.db.get_value("Customer", {"custom_qoyod_id": str(qid)}, "name")

	def item(qid):
		return frappe.db.get_value("Item", {"custom_qoyod_id": str(qid)}, "name")

	quotes = json.load(open(os.path.join(data_dir(), "quotes.json"), encoding="utf-8"))
	if limit:
		quotes = quotes[:limit]

	created = skipped = errors = 0
	err_samples = []
	print("=" * 56)
	print(f"Qoyod quotes -> Quotation  [{'COMMIT' if commit else 'DRY RUN'}]  n={len(quotes)}")
	print("=" * 56)

	for q in quotes:
		qid = str(q["id"])
		try:
			if frappe.db.get_value("Quotation", {"custom_qoyod_id": qid}, "name"):
				skipped += 1
				continue
			customer = cust(q.get("contact_id"))
			if not customer:
				raise ValueError(f"customer contact_id={q.get('contact_id')} not found")

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
				gross = _f(li.get("inclusive_unit_price") if inclusive else li.get("unit_price"))
				disc = _f(li.get("discount_amount"))
				rate = (gross * qty - disc) / qty if qty else (gross - disc)
				row = {"item_code": it, "qty": qty, "rate": rate}
				if cost_center:
					row["cost_center"] = cost_center
				items.append(row)

			taxes = []
			if vat_account and any_tax:
				taxes.append({
					"charge_type": "On Net Total", "account_head": vat_account,
					"description": f"VAT {tax_rate:g}%", "rate": tax_rate,
					"included_in_print_rate": 1 if inclusive else 0,
					"cost_center": cost_center,
				})

			doc = frappe.get_doc({
				"doctype": "Quotation",
				"company": company,
				"currency": currency,
				"quotation_to": "Customer",
				"party_name": customer,
				"transaction_date": q.get("issue_date"),
				"valid_till": q.get("expiry_date") or None,
				"custom_qoyod_id": qid,
				"items": items,
				"taxes": taxes,
			})

			if commit:
				doc.insert(ignore_permissions=True)
				status = (q.get("status") or "").lower()
				if status in ("approved", "invoiced"):
					doc.submit()
				elif status == "cancelled":
					doc.submit()
					doc.cancel()
				created += 1
			else:
				doc.set_missing_values()
				doc.run_method("calculate_taxes_and_totals")
				created += 1

		except Exception as e:  # noqa: BLE001
			errors += 1
			if len(err_samples) < 10:
				err_samples.append(f"quote {qid}: {str(e)[:150]}")

	if commit:
		frappe.db.commit()
	print(f"\n  created: {created}  skipped: {skipped}  errors: {errors}")
	for s in err_samples:
		print("   ", s)
	return {"created": created, "skipped": skipped, "errors": errors}
