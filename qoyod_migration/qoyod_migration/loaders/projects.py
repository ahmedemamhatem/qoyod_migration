"""
Qoyod projects -> ERPNext Project
=================================

Qoyod project fields: id, reference (PRJ-N), name, description.
-> ERPNext Project: project_name = name (fallback reference), notes = description.

Idempotent (Project.custom_qoyod_id). Also ensures the custom field exists.
DRY RUN by default.
"""

import json
import os

from qoyod_migration.qoyod_migration import config
from qoyod_migration.qoyod_migration.config import data_dir


def ensure_field():
	import frappe
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
	create_custom_fields({
		"Project": [{
			"fieldname": "custom_qoyod_id", "label": "Qoyod ID", "fieldtype": "Data",
			"read_only": 1, "insert_after": "project_name",
			"description": "Source project id in Qoyod.",
		}],
	}, update=True)
	frappe.db.commit()


def build(commit=False):
	import frappe

	ensure_field() if commit else None

	projects = json.load(open(os.path.join(data_dir(), "projects.json"), encoding="utf-8"))
	created = skipped = errors = 0
	err_samples = []
	print("=" * 56)
	print(f"Qoyod projects -> Project  [{'COMMIT' if commit else 'DRY RUN'}]  n={len(projects)}")
	print("=" * 56)

	for q in projects:
		qid = str(q["id"])
		try:
			if frappe.db.get_value("Project", {"custom_qoyod_id": qid}, "name"):
				skipped += 1
				continue
			name = (q.get("name") or q.get("reference") or f"Qoyod Project {qid}").strip()
			vals = {
				"doctype": "Project",
				"project_name": name,
				"custom_qoyod_id": qid,
				"notes": q.get("description") or None,
			}
			if commit:
				# find-or-adopt by name too (Project autoname may collide)
				existing_by_name = frappe.db.get_value("Project", {"project_name": name}, "name")
				if existing_by_name:
					frappe.db.set_value("Project", existing_by_name, "custom_qoyod_id", qid,
					                    update_modified=False)
					skipped += 1
					continue
				frappe.get_doc(vals).insert(ignore_permissions=True)
			created += 1
		except Exception as e:  # noqa: BLE001
			errors += 1
			if len(err_samples) < 10:
				err_samples.append(f"project {qid}: {str(e)[:140]}")

	if commit:
		frappe.db.commit()
	print(f"\n  created: {created}  skipped: {skipped}  errors: {errors}")
	for s in err_samples:
		print("   ", s)
	return {"created": created, "skipped": skipped, "errors": errors}


def link_journal_entries(commit=False):
	"""Set the ERPNext JE 'project' field from the Qoyod project_id on entries."""
	import frappe

	je = json.load(open(os.path.join(data_dir(), "journal_entries.json"), encoding="utf-8"))
	proj_map = {}  # qoyod project id -> ERPNext Project name

	def proj(qpid):
		if qpid not in proj_map:
			proj_map[qpid] = frappe.db.get_value("Project", {"custom_qoyod_id": str(qpid)}, "name")
		return proj_map[qpid]

	linked = 0
	for j in je:
		pid = j.get("project_id")
		if not pid:
			continue
		je_name = frappe.db.get_value("Journal Entry", {"custom_qoyod_id": str(j["id"])}, "name")
		if not je_name:
			continue
		erp_proj = proj(pid)
		if not erp_proj:
			continue
		if commit:
			# Journal Entry has no header 'project'; it lives on the account lines.
			for row in frappe.get_all("Journal Entry Account",
			                          {"parent": je_name}, "name"):
				frappe.db.set_value("Journal Entry Account", row.name, "project",
				                    erp_proj, update_modified=False)
		linked += 1
	if commit:
		frappe.db.commit()
	print(f"  linked {linked} journal entries to their project")
	return linked
