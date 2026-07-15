"""
Qoyod -> ERPNext custom field definitions
=========================================

Every Qoyod attribute that has no home in a standard ERPNext field gets a
custom field here, so the sync is LOSSLESS. Run once (idempotent) before the
loaders. Uses Frappe's create_custom_fields, so these round-trip cleanly through
the fixtures exporter if you want to ship them from another app.

These are created automatically on install/migrate (see install.py) and again at
the start of every commit run (orchestrator.setup_custom_fields).
"""

# fieldname -> definition. `insert_after` keeps the desk form tidy.
CUSTOM_FIELDS = {
    "Customer": [
        {"fieldname": "custom_qoyod_id", "label": "Qoyod ID", "fieldtype": "Data",
         "read_only": 1, "unique": 1, "insert_after": "tax_id",
         "description": "Source record id in Qoyod. Do not edit."},
        {"fieldname": "custom_secondary_phone", "label": "Secondary Phone",
         "fieldtype": "Data", "insert_after": "mobile_no"},
        {"fieldname": "custom_government_entity", "label": "Government Entity",
         "fieldtype": "Check", "insert_after": "custom_secondary_phone"},
        {"fieldname": "custom_qoyod_billing_json", "label": "Qoyod Billing Address (raw)",
         "fieldtype": "Small Text", "read_only": 1, "insert_after": "custom_government_entity"},
        {"fieldname": "custom_qoyod_bank_json", "label": "Qoyod Bank (raw)",
         "fieldtype": "Small Text", "read_only": 1, "insert_after": "custom_qoyod_billing_json"},
    ],
    "Supplier": [
        {"fieldname": "custom_qoyod_id", "label": "Qoyod ID", "fieldtype": "Data",
         "read_only": 1, "unique": 1, "insert_after": "tax_id",
         "description": "Source record id in Qoyod. Do not edit."},
        {"fieldname": "custom_secondary_phone", "label": "Secondary Phone",
         "fieldtype": "Data", "insert_after": "mobile_no"},
        {"fieldname": "custom_government_entity", "label": "Government Entity",
         "fieldtype": "Check", "insert_after": "custom_secondary_phone"},
        {"fieldname": "custom_qoyod_billing_json", "label": "Qoyod Billing Address (raw)",
         "fieldtype": "Small Text", "read_only": 1, "insert_after": "custom_government_entity"},
    ],
    "Item": [
        {"fieldname": "custom_qoyod_id", "label": "Qoyod ID", "fieldtype": "Data",
         "read_only": 1, "unique": 1, "insert_after": "item_code",
         "description": "Source record id in Qoyod. Do not edit."},
        {"fieldname": "custom_name_ar", "label": "Name (Arabic)", "fieldtype": "Data",
         "insert_after": "item_name"},
        {"fieldname": "custom_barcode", "label": "Qoyod Barcode", "fieldtype": "Data",
         "insert_after": "custom_name_ar"},
        {"fieldname": "custom_buying_price", "label": "Qoyod Buying Price",
         "fieldtype": "Currency", "insert_after": "standard_rate"},
        {"fieldname": "custom_qoyod_type", "label": "Qoyod Type", "fieldtype": "Select",
         "options": "\nProduct\nService\nExpense", "insert_after": "custom_buying_price"},
    ],
    "UOM": [
        {"fieldname": "custom_qoyod_id", "label": "Qoyod ID", "fieldtype": "Data",
         "read_only": 1, "unique": 1, "insert_after": "uom_name",
         "description": "Source record id in Qoyod. Do not edit."},
    ],
    "Item Group": [
        {"fieldname": "custom_qoyod_id", "label": "Qoyod ID", "fieldtype": "Data",
         "read_only": 1, "unique": 1, "insert_after": "item_group_name",
         "description": "Source record id in Qoyod. Do not edit."},
    ],
    "Account": [
        {"fieldname": "custom_qoyod_id", "label": "Qoyod ID", "fieldtype": "Data",
         "read_only": 1, "insert_after": "account_number",
         "description": "Source account id in Qoyod. Do not edit."},
        {"fieldname": "custom_qoyod_code", "label": "Qoyod Code", "fieldtype": "Data",
         "read_only": 1, "insert_after": "custom_qoyod_id",
         "description": "Original account code in Qoyod."},
    ],
    # Transaction doctypes -- tagged so imports are idempotent and traceable.
    "Sales Invoice": [
        {"fieldname": "custom_qoyod_id", "label": "Qoyod ID", "fieldtype": "Data",
         "read_only": 1, "insert_after": "title",
         "description": "Source invoice id in Qoyod. Do not edit."},
    ],
    "Purchase Invoice": [
        {"fieldname": "custom_qoyod_id", "label": "Qoyod ID", "fieldtype": "Data",
         "read_only": 1, "insert_after": "title",
         "description": "Source bill id in Qoyod. Do not edit."},
    ],
    "Journal Entry": [
        {"fieldname": "custom_qoyod_id", "label": "Qoyod ID", "fieldtype": "Data",
         "read_only": 1, "insert_after": "title",
         "description": "Source journal entry id in Qoyod. Do not edit."},
    ],
    "Payment Entry": [
        {"fieldname": "custom_qoyod_id", "label": "Qoyod ID", "fieldtype": "Data",
         "read_only": 1, "insert_after": "title",
         "description": "Source receipt id in Qoyod. Do not edit."},
    ],
}


def create_all():
    """Idempotently create every custom field above. Call inside a Frappe context."""
    import frappe
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

    create_custom_fields(CUSTOM_FIELDS, update=True)
    frappe.db.commit()

    # Report
    for dt, fields in CUSTOM_FIELDS.items():
        for f in fields:
            name = f"{dt}-{f['fieldname']}"
            ok = frappe.db.exists("Custom Field", name)
            print(f"  {'OK ' if ok else 'ERR'} {name}")
    print(f"\nTotal custom fields defined: {sum(len(v) for v in CUSTOM_FIELDS.values())}")


# List of "DocType-fieldname" names, for the fixtures filter in hooks.py
def fixture_names():
    return [f"{dt}-{f['fieldname']}" for dt, fields in CUSTOM_FIELDS.items() for f in fields]
