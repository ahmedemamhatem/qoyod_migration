# Copyright (c) 2026, Ahmed Emam and contributors
# For license information, please see license.txt
"""
Qoyod -> ERPNext full sync orchestrator.

The complete end-to-end flow in one function, in strict dependency order,
idempotent (every record carries custom_qoyod_id / custom_qoyod_code; loaders
skip existing). Rewritten from the original run_sync.py WITHOUT the
importlib.reload / sys.path / per-module-DATA_DIR hacks -- everything is a clean
package import and reads paths/keys from `config`.

Sequence:
  1 CONNECT   2 EXTRACT   3 SETUP custom fields   4 ACCOUNTS   5 MASTERS
  5b PROJECTS 6 ADDRESSES/CONTACTS + company ZATCA + fiscal years + JE fix
  7 TRANSACTIONS (Quotations, Sales Invoices, Purchase Invoices, Credit Notes,
    Journal Entries, Payments; then link JEs to projects)
  8 BACKFILL Qoyod Data
"""

import base64

import frappe
import requests

from qoyod_migration.qoyod_migration import config
from qoyod_migration.qoyod_migration.custom_fields import (
	base_fields,
	master_fields,
	misc_fields,
	txn_fields,
)
from qoyod_migration.qoyod_migration.extract import extractor
from qoyod_migration.qoyod_migration.loaders import (
	accounts,
	addresses,
	credit_notes,
	journal_entries,
	masters,
	payments,
	projects,
	purchase_invoices,
	quotes,
	sales_invoices,
)


def _banner(step, title):
	print("\n" + "=" * 64)
	print(f"  STEP {step}: {title}")
	print("=" * 64)


# --------------------------------------------------------------------------
# Step 1 - connection test
# --------------------------------------------------------------------------

def connect():
	key = config.get_api_key()
	base = config.get_api_base()
	s = requests.Session()
	s.headers.update({"API-KEY": key, "Accept": "application/json"})
	r = s.get(f"{base}/customers", params={"page": 1, "per_page": 1}, timeout=30)
	if r.status_code != 200:
		frappe.throw(f"Qoyod connection failed: HTTP {r.status_code} {r.text[:200]}")
	print(f"   connected OK (HTTP 200); sample keys: {list(r.json().keys())}")
	return True


# --------------------------------------------------------------------------
# Prerequisites (company ZATCA + fiscal years + JE account-type fix)
# --------------------------------------------------------------------------

def _ensure_company_zatca():
	"""ksa_vat blocks Sales Invoice submit unless the Company has
	company_name_in_arabic + tax_id; read seller name/VAT from an invoice ZATCA QR."""
	company = config.get_company()
	if frappe.db.get_value("Company", company, "company_name_in_arabic") and \
	   frappe.db.get_value("Company", company, "tax_id"):
		return
	seller_name = seller_vat = None
	for inv in config.load_json("invoices"):
		qr = inv.get("qrcode_string")
		if not qr:
			continue
		try:
			raw = base64.b64decode(qr)
			i = 0
			while i < len(raw):
				tag, ln = raw[i], raw[i + 1]
				val = raw[i + 2:i + 2 + ln]
				i += 2 + ln
				if tag == 1:
					seller_name = val.decode("utf-8", "ignore")
				elif tag == 2:
					seller_vat = val.decode("utf-8", "ignore")
			if seller_name and seller_vat:
				break
		except Exception:  # noqa: BLE001
			continue
	if seller_name and not frappe.db.get_value("Company", company, "company_name_in_arabic"):
		frappe.db.set_value("Company", company, "company_name_in_arabic", seller_name)
	if seller_vat and not frappe.db.get_value("Company", company, "tax_id"):
		frappe.db.set_value("Company", company, "tax_id", seller_vat)
	frappe.db.commit()
	print("   company ZATCA fields ensured")


def _ensure_fiscal_years():
	company = config.get_company()
	years = set()
	for res in ("invoices", "bills", "journal_entries", "receipts", "credit_notes"):
		for r in config.load_json(res):
			d = r.get("issue_date") or r.get("date") or ""
			if d[:4].isdigit():
				years.add(int(d[:4]))
	for yr in sorted(years):
		if frappe.db.exists("Fiscal Year", str(yr)):
			continue
		fy = frappe.get_doc({"doctype": "Fiscal Year", "year": str(yr),
		                     "year_start_date": f"{yr}-01-01", "year_end_date": f"{yr}-12-31"})
		fy.insert(ignore_permissions=True)
		fy.append("companies", {"company": company})
		fy.save(ignore_permissions=True)
	frappe.db.commit()
	if years:
		print(f"   fiscal years ensured: {sorted(years)}")


def _fix_account_types_for_je():
	"""Qoyod JE lines have no party; clear Receivable/Payable account_type on the
	-Qoyod accounts so party-less lines post."""
	company = config.get_company()
	affected = frappe.get_all("Account", {
		"company": company, "custom_qoyod_id": ["is", "set"],
		"account_type": ["in", ["Receivable", "Payable"]]}, ["name"])
	for a in affected:
		frappe.db.set_value("Account", a.name, "account_type", "")
	if affected:
		frappe.db.commit()
		print(f"   cleared Receivable/Payable type on {len(affected)} Qoyod accounts")


# --------------------------------------------------------------------------
# The runner
# --------------------------------------------------------------------------

def setup_custom_fields():
	"""Create every Qoyod custom field (id keys, Qoyod Data sections, misc ids)."""
	base_fields.create_all()
	master_fields.create(commit=True)
	txn_fields.create(commit=True)
	misc_fields.create(commit=True)


def run(commit=False, extract=True, skip_transactions=False):
	"""
	commit=False -> dry run (connect + extract, validate, write nothing).
	commit=True  -> insert everything.
	extract=True -> pull fresh data from Qoyod first; False -> reuse shipped data/*.json.
	"""
	frappe.set_user("Administrator")
	mode = "COMMIT (writing)" if commit else "DRY RUN (no writes)"
	print(f"\n{'#' * 64}\n#  QOYOD -> ERPNext FULL SYNC   [{mode}]\n{'#' * 64}")

	# 1) CONNECT + 2) EXTRACT
	if extract:
		_banner(1, "CONNECT to Qoyod")
		connect()
		_banner(2, "EXTRACT all Qoyod data")
		extractor.main()
	else:
		print("\n(extract=False -> reusing shipped data/*.json)")

	# 3) SETUP custom fields
	_banner(3, "SETUP custom fields")
	if commit:
		setup_custom_fields()
	else:
		print("   (dry run) custom-field creation skipped")

	# 4) ACCOUNTS
	_banner(4, "ACCOUNTS (Qoyod chart -> -Qoyod)")
	accounts.build(commit=commit)

	# 5) MASTERS
	_banner(5, "MASTERS (UOM/Item Group/Customer/Supplier/Item)")
	masters.main(commit=commit)

	# 5b) PROJECTS
	print("\n--- Projects ---")
	projects.build(commit=commit)

	# 6) ADDRESSES / CONTACTS
	_banner(6, "ADDRESSES / CONTACTS")
	addresses.build(commit=commit)

	# prerequisites before posting transactions
	if commit:
		_ensure_company_zatca()
		_ensure_fiscal_years()
		_fix_account_types_for_je()

	# 7) TRANSACTIONS
	if not skip_transactions:
		_banner(7, "TRANSACTIONS")
		print("\n--- Quotations ---"); quotes.build(commit=commit)
		print("\n--- Sales Invoices ---"); sales_invoices.build(commit=commit)
		print("\n--- Purchase Invoices ---"); purchase_invoices.build(commit=commit)
		print("\n--- Credit Notes ---"); credit_notes.build(commit=commit)
		print("\n--- Journal Entries ---"); journal_entries.build(commit=commit)
		print("\n--- Payments ---"); payments.build(commit=commit)
		if commit:
			print("\n--- Link JEs to Projects ---")
			projects.link_journal_entries(commit=True)
	else:
		print("\n(skip_transactions=True)")

	# 8) BACKFILL Qoyod Data
	if commit:
		_banner(8, "BACKFILL Qoyod Data (db.set_value)")
		master_fields.backfill(commit=True)
		txn_fields.backfill(commit=True)

	print(f"\n{'#' * 64}\n#  DONE  [{mode}]\n{'#' * 64}")
	if not commit:
		print("Re-run with commit=True to apply.")
