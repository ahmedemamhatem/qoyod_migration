# Copyright (c) 2026, Ahmed Emam and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class QoyodMigrationSettings(Document):
	@frappe.whitelist()
	def test_connection(self):
		from qoyod_migration.qoyod_migration import orchestrator

		orchestrator.connect()
		return "Connected to Qoyod (HTTP 200)."

	@frappe.whitelist()
	def run_sync(self, commit=0, extract=0):
		"""Queue the full sync as a background job."""
		from qoyod_migration.qoyod_migration.api import enqueue_full_sync

		enqueue_full_sync(commit=commit, extract=extract)
		return "Sync queued as a background job. Check Qoyod Sync Log / the worker."
