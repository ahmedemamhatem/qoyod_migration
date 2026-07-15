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
import io
import traceback
from contextlib import redirect_stdout

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


def _run_body(commit, extract, skip_transactions, steps):
	"""The 8-step sequence. Appends {step, dt, created, skipped, errors} dicts to
	`steps` as loaders return their result dicts, so the Sync Log can record them."""

	def record(step, dt, result):
		result = result or {}
		steps.append({
			"step": step,
			"reference_doctype": dt,
			"created": int(result.get("created") or result.get("addr") or 0),
			"skipped": int(result.get("skipped") or 0),
			"errors": int(result.get("errors") or 0),
		})

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
		if not config.has_dump("customers"):
			frappe.throw(
				"extract=False but no data/*.json dumps are present. "
				"Run with extract=True to pull from Qoyod first."
			)

	# 3) SETUP custom fields
	_banner(3, "SETUP custom fields")
	if commit:
		setup_custom_fields()
	else:
		print("   (dry run) custom-field creation skipped")

	# 4) ACCOUNTS
	_banner(4, "ACCOUNTS (Qoyod chart -> suffixed)")
	accounts.build(commit=commit)

	# 5) MASTERS
	_banner(5, "MASTERS (UOM/Item Group/Customer/Supplier/Item)")
	masters.main(commit=commit)

	# 5b) PROJECTS
	print("\n--- Projects ---")
	record("Projects", "Project", projects.build(commit=commit))

	# 6) ADDRESSES / CONTACTS
	_banner(6, "ADDRESSES / CONTACTS")
	record("Addresses/Contacts", "Address/Contact", addresses.build(commit=commit))

	# prerequisites before posting transactions
	if commit:
		_ensure_company_zatca()
		_ensure_fiscal_years()
		_fix_account_types_for_je()

	# 7) TRANSACTIONS
	if not skip_transactions:
		_banner(7, "TRANSACTIONS")
		print("\n--- Quotations ---"); record("Quotations", "Quotation", quotes.build(commit=commit))
		print("\n--- Sales Invoices ---"); record("Sales Invoices", "Sales Invoice", sales_invoices.build(commit=commit))
		print("\n--- Purchase Invoices ---"); record("Purchase Invoices", "Purchase Invoice", purchase_invoices.build(commit=commit))
		print("\n--- Credit Notes ---"); record("Credit Notes", "Sales Invoice (return)", credit_notes.build(commit=commit))
		print("\n--- Journal Entries ---"); record("Journal Entries", "Journal Entry", journal_entries.build(commit=commit))
		print("\n--- Payments ---"); record("Payments", "Payment Entry", payments.build(commit=commit))
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


def run(commit=False, extract=True, skip_transactions=False, log=True):
	"""
	commit=False -> dry run (connect + extract, validate, write nothing).
	commit=True  -> insert everything.
	extract=True -> pull fresh data from Qoyod first; False -> reuse shipped data/*.json.
	log=True     -> record the run in a Qoyod Sync Log (steps, console output, status).

	Always restores the original session user, and never raises out of the log
	bookkeeping -- a failure in the sequence is captured on the log and re-raised.
	"""
	# Restore whatever user was active on entry (default to Administrator if the
	# session somehow has none), so we never leak an elevated context.
	prev_user = (frappe.session.user if frappe.session else None) or "Administrator"
	frappe.set_user("Administrator")

	steps = []
	buf = io.StringIO()
	log_doc = None
	if log:
		log_doc = frappe.get_doc({
			"doctype": "Qoyod Sync Log",
			"run_datetime": frappe.utils.now_datetime(),
			"status": "Running",
			"mode": "Commit" if commit else "Dry Run",
			"extract": 1 if extract else 0,
			"skip_transactions": 1 if skip_transactions else 0,
			"triggered_by": prev_user,
		})
		try:
			log_doc.insert(ignore_permissions=True)
			frappe.db.commit()
		except Exception:  # noqa: BLE001 -- logging must never break the sync
			log_doc = None

	error = None
	try:
		with redirect_stdout(buf):
			_run_body(commit, extract, skip_transactions, steps)
	except Exception:  # noqa: BLE001
		error = traceback.format_exc()
		print(error)
	finally:
		print(buf.getvalue())  # surface captured output to the real console/worker log
		if log_doc:
			_finalize_log(log_doc, steps, buf.getvalue(), error)
		frappe.set_user(prev_user)

	if error:
		frappe.throw("Qoyod sync failed; see the Qoyod Sync Log for the full traceback.")
	return {"ok": True, "log": log_doc.name if log_doc else None, "steps": steps}


def _finalize_log(log_doc, steps, output, error):
	"""Persist step rows, console output, and final status. Never raises."""
	try:
		for s in steps:
			log_doc.append("results", s)
		total_errors = sum(s["errors"] for s in steps)
		log_doc.finished_datetime = frappe.utils.now_datetime()
		log_doc.log_output = output[-100000:]  # keep the tail if huge
		if error:
			log_doc.status = "Failed"
			log_doc.error_log = error
		elif total_errors:
			log_doc.status = "Partial"
		else:
			log_doc.status = "Success"
		log_doc.save(ignore_permissions=True)
		frappe.db.commit()
	except Exception:  # noqa: BLE001
		frappe.db.rollback()
