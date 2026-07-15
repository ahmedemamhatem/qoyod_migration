"""
Qoyod Data section on MASTER doctypes (Customer/Supplier/Item/Account)
=====================================================================

Adds a collapsible, read-only, 4-column "Qoyod Data" section holding the Qoyod
source fields that have NO standard-field home yet (per audit). Placed safely at
the end of the form. Then backfills every imported master.
"""

import json
import os

from qoyod_migration.qoyod_migration.config import data_dir

# doctype -> (source json, key builder, anchor field, [(suffix,label,ftype), ...])
CONFIG = {
    "Customer": {
        "src": "customers", "anchor": "portal_users",
        "fields": [
            ("organization", "Qoyod Organization", "Data"),
            ("status", "Qoyod Status", "Data"),
            ("pos", "Qoyod POS", "Check"),
            ("customer_details", "Qoyod Customer Details", "Small Text"),
            ("custom_fields", "Qoyod Custom Fields", "Small Text"),
            ("created_at", "Qoyod Created At", "Data"),
            ("updated_at", "Qoyod Updated At", "Data"),
        ],
    },
    "Supplier": {
        "src": "vendors", "anchor": "column_break_1mqv",
        "fields": [
            ("organization", "Qoyod Organization", "Data"),
            ("status", "Qoyod Status", "Data"),
            ("pos", "Qoyod POS", "Check"),
            ("customer_details", "Qoyod Details", "Small Text"),
            ("custom_fields", "Qoyod Custom Fields", "Small Text"),
            ("created_at", "Qoyod Created At", "Data"),
            ("updated_at", "Qoyod Updated At", "Data"),
        ],
    },
    "Item": {
        "src": "products", "anchor": "total_projected_qty",
        "fields": [
            ("unit_type", "Qoyod Unit Type", "Data"),
            ("is_buying_price_inclusive", "Qoyod Buying Price Inclusive", "Check"),
            ("is_selling_price_inclusive", "Qoyod Selling Price Inclusive", "Check"),
            ("is_sold", "Qoyod Is Sold", "Check"),
            ("is_bought", "Qoyod Is Bought", "Check"),
            ("pos_product", "Qoyod POS Product", "Check"),
            ("tax_id", "Qoyod Tax ID", "Data"),
            ("sales_account_id", "Qoyod Sales Account ID", "Data"),
            ("expense_account_id", "Qoyod Expense Account ID", "Data"),
            ("inventories", "Qoyod Inventories", "Small Text"),
            ("unit_conversions", "Qoyod Unit Conversions", "Small Text"),
            ("ingredients", "Qoyod Ingredients", "Small Text"),
            ("terms_and_conditions", "Qoyod Terms", "Small Text"),
            ("special_tax_reason_id", "Qoyod Special Tax Reason", "Data"),
            ("created_at", "Qoyod Created At", "Data"),
            ("updated_at", "Qoyod Updated At", "Data"),
        ],
    },
    "Account": {
        "src": "accounts", "anchor": "include_in_gross",
        "fields": [
            ("group_type", "Qoyod Group Type", "Data"),
            ("status", "Qoyod Status", "Data"),
            ("account_nature", "Qoyod Account Nature", "Data"),
            ("type_of_account", "Qoyod Type Of Account", "Data"),
            ("parent_type", "Qoyod Parent Type", "Data"),
            ("balance", "Qoyod Balance", "Data"),
            ("deferral_template_id", "Qoyod Deferral Template", "Data"),
            ("created_at", "Qoyod Created At", "Data"),
            ("updated_at", "Qoyod Updated At", "Data"),
        ],
    },
}

N_COLS = 4


def _defs():
    out = {}
    for dt, cfg in CONFIG.items():
        # A Tab Break starts a brand-new tab at the end of the form and CANNOT
        # swallow existing sections/fields (unlike a Section Break mid-form).
        rows = [{
            "fieldname": "custom_qoyod_data_tab",
            "label": "Qoyod Data",
            "fieldtype": "Tab Break",
            "insert_after": cfg["anchor"],
        }]
        prev = "custom_qoyod_data_tab"
        fields = cfg["fields"]
        n = len(fields)
        per_col = -(-n // N_COLS)
        col_starts = set(range(0, n, per_col))
        for i, (suffix, label, ftype) in enumerate(fields):
            if i in col_starts and i != 0:
                cb = f"custom_qoyod_mcol_{i}"
                rows.append({"fieldname": cb, "fieldtype": "Column Break", "insert_after": prev})
                prev = cb
            fn = f"custom_qoyod_{suffix}"
            rows.append({
                "fieldname": fn, "label": label, "fieldtype": ftype,
                "insert_after": prev, "read_only": 1, "allow_on_submit": 0, "translatable": 0,
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
    total = sum(len(c["fields"]) for c in CONFIG.values())
    print(f"Created master Qoyod Data section (4 cols) + {total} fields across {len(CONFIG)} doctypes.")


def _val(v):
    if v in (None, "", [], {}):
        return None
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def qoyod_values(dt, record):
    """Shared source-of-truth: Qoyod master record -> {custom_qoyod_*: value}.
    Master loaders merge this into the doc BEFORE insert (fill-on-insert)."""
    cfg = CONFIG.get(dt)
    if not cfg:
        return {}
    out = {}
    for suffix, _l, ft in cfg["fields"]:
        if suffix in record:
            val = _val(record.get(suffix))
            if ft == "Check" and val is None:
                val = 0
            out[f"custom_qoyod_{suffix}"] = val
    return out


def backfill(commit=True):
    """Backfill Qoyod Data on masters via frappe.db.set_value (direct write,
    update_modified=False). Loaders fill on insert; this is a top-up/repair pass."""
    import frappe
    for dt, cfg in CONFIG.items():
        recs = json.load(open(os.path.join(data_dir(), f"{cfg['src']}.json"), encoding="utf-8"))
        key = "custom_qoyod_code" if dt == "Account" else "custom_qoyod_id"
        src_key = "code" if dt == "Account" else "id"
        touched = 0
        for r in recs:
            name = frappe.db.get_value(dt, {key: str(r[src_key])}, "name")
            if not name:
                continue
            vals = qoyod_values(dt, r)
            if not vals:
                continue
            frappe.db.set_value(dt, name, vals, update_modified=False)
            touched += 1
        if commit:
            frappe.db.commit()
        print(f"  backfilled {touched} {dt} (db.set_value)")


def fixture_names():
    out = []
    for dt, cfg in CONFIG.items():
        out.append(f"{dt}-custom_qoyod_data_tab")
        for suffix, _l, _t in cfg["fields"]:
            out.append(f"{dt}-custom_qoyod_{suffix}")
    return out
