# Copyright (c) 2026, Ahmed Emam and contributors
# For license information, please see license.txt
"""
Install / migrate hooks: keep the Qoyod custom fields in sync on every install
and migrate (idempotent). The custom/*.json (sync_on_migrate) files are a second,
declarative guarantee; this programmatic pass is the source of truth for the
computed 4-column "Qoyod Data" layouts.
"""

import frappe


def after_install():
	setup_custom_fields()


def after_migrate():
	setup_custom_fields()


def setup_custom_fields():
	from qoyod_migration.qoyod_migration.custom_fields import (
		base_fields,
		master_fields,
		misc_fields,
		txn_fields,
	)

	base_fields.create_all()
	master_fields.create(commit=True)
	txn_fields.create(commit=True)
	misc_fields.create(commit=True)
	frappe.db.commit()
