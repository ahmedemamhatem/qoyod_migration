# Copyright (c) 2026, Ahmed Emam and contributors
# For license information, please see license.txt
"""
custom_qoyod_id on the "extra" doctypes that the loaders create records in but
which the three main field modules don't cover: Address, Contact, Project,
Quotation.

Previously Address/Contact ids existed only as exported JSON, and Project/Quotation
were created lazily inside the loaders' ensure_field(). Consolidating them here --
called by install.py after_migrate -- guarantees every custom_qoyod_id exists
before any loader runs, regardless of run order.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

FIELDS = {
	"Address": [
		{"fieldname": "custom_qoyod_id", "label": "Qoyod ID", "fieldtype": "Data",
		 "read_only": 1, "insert_after": "address_title"},
	],
	"Contact": [
		{"fieldname": "custom_qoyod_id", "label": "Qoyod ID", "fieldtype": "Data",
		 "read_only": 1, "insert_after": "first_name"},
	],
	"Project": [
		{"fieldname": "custom_qoyod_id", "label": "Qoyod ID", "fieldtype": "Data",
		 "read_only": 1, "insert_after": "project_name",
		 "description": "Source project id in Qoyod."},
	],
	"Quotation": [
		{"fieldname": "custom_qoyod_id", "label": "Qoyod ID", "fieldtype": "Data",
		 "read_only": 1, "insert_after": "title"},
	],
}


def create(commit=True):
	create_custom_fields(FIELDS, update=True)
	if commit:
		frappe.db.commit()
	print(f"Created custom_qoyod_id on {', '.join(FIELDS)}.")
