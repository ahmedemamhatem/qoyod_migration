"""
Lossless Qoyod fields on transaction DocTypes  (v2 - safe placement)
====================================================================

v1 anchored the section after `title`, which sat inside the invoice's first
section and swallowed customer/posting_date/etc -> broke the form.

v2 places a dedicated, collapsible, READ-ONLY "Qoyod Data" section at the END
of the form (inside the More Info tab for Sales/Purchase Invoice; at form end
for Journal Entry / Payment Entry), laid out in FOUR COLUMNS. It never touches
any standard field's section.

Field values live in DB columns (custom_qoyod_*) and are unaffected by
recreating these Custom Field definitions.
"""

import json
import os

from qoyod_migration.qoyod_migration.config import data_dir

# Anchor the Qoyod section AFTER this field (a safe end-of-content spot).
SECTION_AFTER = {
    "Sales Invoice": "remarks",       # inside More Info tab
    "Purchase Invoice": "remarks",    # inside More Info tab
    "Journal Entry": "amended_from",  # end of form
    "Payment Entry": "title",         # PE has no tabs; title is first — section still ends form cleanly
}

# custom_qoyod_id is the traceability key; it becomes the FIRST field of the section.
# All fields listed here (id first) are distributed across 4 columns.
FIELDSETS = {
    "Sales Invoice": [
        ("id", "Qoyod ID", "Data"),
        ("reference", "Qoyod Reference", "Data"),
        ("status", "Qoyod Status", "Data"),
        ("supply_date", "Qoyod Supply Date", "Data"),
        ("due_amount", "Qoyod Due Amount", "Data"),
        ("paid_amount", "Qoyod Paid Amount", "Data"),
        ("remaining_amount", "Qoyod Remaining Amount", "Data"),
        ("payment_method", "Qoyod Payment Method", "Data"),
        ("contract_id", "Qoyod Contract ID", "Data"),
        ("created_by", "Qoyod Created By", "Data"),
        ("created_at", "Qoyod Created At", "Data"),
        ("updated_at", "Qoyod Updated At", "Data"),
        ("inclusive_cd_discount", "Qoyod Inclusive CD Discount", "Data"),
        ("discount_account_id", "Qoyod Discount Account ID", "Data"),
        ("discount_tax_id", "Qoyod Discount Tax ID", "Data"),
        ("issuance_reason", "Qoyod Issuance Reason", "Small Text"),
        ("notes", "Qoyod Notes", "Small Text"),
        ("terms_conditions", "Qoyod Terms", "Small Text"),
        ("qrcode_string", "Qoyod ZATCA QR", "Small Text"),
        ("zatca_details", "Qoyod ZATCA Details", "Small Text"),
    ],
    "Purchase Invoice": [
        ("id", "Qoyod ID", "Data"),
        ("status", "Qoyod Status", "Data"),
        ("due_amount", "Qoyod Due Amount", "Data"),
        ("paid_amount", "Qoyod Paid Amount", "Data"),
        ("created_by", "Qoyod Created By", "Data"),
        ("created_at", "Qoyod Created At", "Data"),
        ("updated_at", "Qoyod Updated At", "Data"),
        ("inclusive_cd_discount", "Qoyod Inclusive CD Discount", "Data"),
        ("discount_account_id", "Qoyod Discount Account ID", "Data"),
        ("discount_tax_id", "Qoyod Discount Tax ID", "Data"),
        ("notes", "Qoyod Notes", "Small Text"),
        ("terms_conditions", "Qoyod Terms", "Small Text"),
    ],
    "Journal Entry": [
        ("id", "Qoyod ID", "Data"),
        ("inventory_id", "Qoyod Inventory ID", "Data"),
        ("project_id", "Qoyod Project ID", "Data"),
        ("custom_fields", "Qoyod Custom Fields", "Small Text"),
    ],
    "Payment Entry": [
        ("id", "Qoyod ID", "Data"),
        ("reference", "Qoyod Reference", "Data"),
        ("created_at", "Qoyod Created At", "Data"),
        ("updated_at", "Qoyod Updated At", "Data"),
        ("description", "Qoyod Description", "Small Text"),
    ],
}

N_COLS = 4


def _defs():
    """Section break + 4-column layout of read-only fields."""
    out = {}
    for dt, fields in FIELDSETS.items():
        rows = [{
            "fieldname": "custom_qoyod_section",
            "label": "Qoyod Data",
            "fieldtype": "Section Break",
            "collapsible": 1,
            "insert_after": SECTION_AFTER[dt],
        }]
        prev = "custom_qoyod_section"

        # Distribute fields across N_COLS columns as evenly as possible.
        n = len(fields)
        per_col = -(-n // N_COLS)  # ceil
        col_starts = set(range(0, n, per_col))  # index where a new column begins

        for i, (suffix, label, ftype) in enumerate(fields):
            # start a new column (except before the very first field; the section
            # itself opens column 1)
            if i in col_starts and i != 0:
                cbname = f"custom_qoyod_col_{i}"
                rows.append({
                    "fieldname": cbname, "fieldtype": "Column Break",
                    "insert_after": prev,
                })
                prev = cbname
            fn = f"custom_qoyod_{suffix}"
            rows.append({
                "fieldname": fn, "label": label, "fieldtype": ftype,
                "insert_after": prev, "read_only": 1, "allow_on_submit": 0,
                "translatable": 0,
            })
            prev = fn
        out[dt] = rows
    return out


def create(commit=True):
    import frappe
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
    create_custom_fields(_defs(), update=True)
    if commit:
        frappe.db.commit()
    total = sum(len(v) for v in FIELDSETS.values())
    print(f"Created Qoyod Data section (4 cols) + {total} fields across {len(FIELDSETS)} doctypes.")


def _val(v):
    if v in (None, "", [], {}):
        return None
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def qoyod_values(fskey, record):
    """
    Shared source-of-truth: map a Qoyod source record -> {custom_qoyod_*: value}
    for the given FIELDSETS key ("Sales Invoice", "Purchase Invoice", ...).

    Loaders merge this into the doc BEFORE insert, so the Qoyod Data section is
    filled on creation (one write, no separate backfill pass). The backfill()
    below reuses it only to fill anything still missing.
    """
    out = {}
    for suffix, _l, _t in FIELDSETS.get(fskey, []):
        if suffix == "id":
            continue  # custom_qoyod_id is set by the loader itself
        if suffix in record:
            out[f"custom_qoyod_{suffix}"] = _val(record.get(suffix))
    return out


def backfill(commit=True):
    """
    Backfill the Qoyod Data fields on already-imported documents using
    frappe.db.set_value (direct DB write, no re-validation/re-submit of the
    posted document; update_modified=False keeps the doc's timestamp).

    Loaders already fill these on insert, so this is a top-up / repair pass for
    documents that predate fill-on-insert. It writes every mapped field for each
    matched document in one bulk update.
    """
    import frappe

    def load(n):
        return json.load(open(os.path.join(data_dir(), f"{n}.json"), encoding="utf-8"))

    plans = [
        ("Sales Invoice", load("invoices"), lambda r: str(r["id"]), "Sales Invoice"),
        ("Sales Invoice", load("credit_notes"), lambda r: f"CN-{r['id']}", "Sales Invoice"),
        ("Purchase Invoice", load("bills"), lambda r: str(r["id"]), "Purchase Invoice"),
        ("Journal Entry", load("journal_entries"), lambda r: str(r["id"]), "Journal Entry"),
        ("Payment Entry", load("receipts"), lambda r: str(r["id"]), "Payment Entry"),
    ]
    for dt, recs, keyfn, fskey in plans:
        touched = 0
        for r in recs:
            qid = keyfn(r)
            name = frappe.db.get_value(dt, {"custom_qoyod_id": qid}, "name")
            if not name:
                continue
            vals = qoyod_values(fskey, r)
            if not vals:
                continue
            # one bulk db.set_value for all mapped fields
            frappe.db.set_value(dt, name, vals, update_modified=False)
            touched += 1
        if commit:
            frappe.db.commit()
        print(f"  backfilled {touched} {dt} (db.set_value)")


def fixture_names():
    names = []
    for dt, fields in FIELDSETS.items():
        names.append(f"{dt}-custom_qoyod_section")
        for suffix, _l, _t in fields:
            names.append(f"{dt}-custom_qoyod_{suffix}")
    return names
