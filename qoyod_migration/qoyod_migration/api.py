# Copyright (c) 2026, Ahmed Emam and contributors
# For license information, please see license.txt
"""
Whitelisted entry points for the Qoyod migration.

    bench --site <site> execute qoyod_migration.qoyod_migration.api.test_connection
    bench --site <site> execute qoyod_migration.qoyod_migration.api.run_full_sync \
        --kwargs "{'commit': True, 'extract': False}"
"""

import frappe
from frappe.utils import cint

from qoyod_migration.qoyod_migration import orchestrator


def _bool(v):
	"""bench execute passes kwargs as strings; coerce to bool."""
	if isinstance(v, bool):
		return v
	if v is None:
		return False
	return bool(cint(v)) if str(v).isdigit() else str(v).strip().lower() in ("true", "yes", "y")


@frappe.whitelist()
def test_connection():
	"""Verify the Qoyod API key/base reach the API (HTTP 200)."""
	frappe.only_for("System Manager")
	orchestrator.connect()
	return {"ok": True}


@frappe.whitelist()
def run_full_sync(commit=False, extract=True, skip_transactions=False):
	"""Run the full Qoyod -> ERPNext sync. See orchestrator.run for semantics."""
	frappe.only_for("System Manager")
	orchestrator.run(
		commit=_bool(commit),
		extract=_bool(extract),
		skip_transactions=_bool(skip_transactions),
	)
	return {"ok": True}


@frappe.whitelist()
def enqueue_full_sync(commit=False, extract=True, skip_transactions=False):
	"""Queue the full sync as a background job (for the Settings UI button)."""
	frappe.only_for("System Manager")
	frappe.enqueue(
		"qoyod_migration.qoyod_migration.api.run_full_sync",
		queue="long",
		timeout=7200,
		commit=commit,
		extract=extract,
		skip_transactions=skip_transactions,
	)
	return {"queued": True}
