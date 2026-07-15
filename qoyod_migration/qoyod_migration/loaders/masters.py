"""
Qoyod masters loader -> ERPNext (via ORM)
=========================================

Loads MASTER DATA ONLY from the local data/*.json dumps into ERPNext, using the
Frappe ORM directly (no REST). Idempotent: every record carries custom_qoyod_id
and we find-or-create on it, so re-runs update instead of duplicating.

Order (dependencies first):
    UOM  ->  Item Group  ->  Customer  ->  Supplier  ->  Item

Group/territory names come from config (Settings -> auto-detected site root ->
default), so this works on English or Arabic ERPNext installs without edits.
Customer type is Company when the record has a tax number, else Individual.

DRY RUN by default. It prints a plan and writes nothing. Pass commit=True to
actually insert/update.
"""

import json
import os

from qoyod_migration.qoyod_migration import config
from qoyod_migration.qoyod_migration.config import data_dir

QID = "custom_qoyod_id"


def _qoyod_values(dt, record):
    """Fill the master Qoyod Data tab fields on insert (shared with backfill)."""
    try:
        from qoyod_migration.qoyod_migration.custom_fields import master_fields as mf
        return mf.qoyod_values(dt, record)
    except Exception:  # noqa: BLE001
        return {}


def _load(name):
    with open(os.path.join(data_dir(), f"{name}.json"), encoding="utf-8") as f:
        return json.load(f)


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


class Stats:
    def __init__(self):
        self.created = self.updated = self.skipped = self.errors = 0

    def line(self, label):
        return (f"  {label:14} created={self.created:>4} "
                f"updated={self.updated:>4} skipped={self.skipped:>4} errors={self.errors:>4}")


# --------------------------------------------------------------------------
# Find-or-create helper keyed on custom_qoyod_id
# --------------------------------------------------------------------------

def upsert(doctype, qoyod_id, values, commit, stats, natural_key=None, natural_val=None):
    """
    Idempotent upsert. Matches first on custom_qoyod_id, then (optionally) on a
    natural key (e.g. an existing UOM by name) so we adopt pre-existing records
    instead of erroring on duplicates.
    Returns the doc name (or a placeholder in dry-run).
    """
    import frappe

    qoyod_id = str(qoyod_id)
    existing = frappe.db.get_value(doctype, {QID: qoyod_id}, "name")
    if not existing and natural_key and natural_val is not None:
        existing = frappe.db.get_value(doctype, {natural_key: natural_val}, "name")

    if existing:
        if not commit:
            stats.skipped += 1  # dry-run counts an update as a no-op preview
            return existing
        doc = frappe.get_doc(doctype, existing)
        for k, v in values.items():
            doc.set(k, v)
        doc.set(QID, qoyod_id)
        doc.save(ignore_permissions=True)
        stats.updated += 1
        return doc.name

    if not commit:
        stats.created += 1
        return f"(new {doctype})"

    doc = frappe.get_doc({"doctype": doctype, QID: qoyod_id, **values})
    doc.insert(ignore_permissions=True)
    stats.created += 1
    return doc.name


# --------------------------------------------------------------------------
# Loaders
# --------------------------------------------------------------------------

def load_uoms(commit, log):
    """UOMs are derived from the distinct product.unit strings."""
    import frappe
    stats = Stats()
    prods = _load("products")
    units = sorted({(p.get("unit") or "").strip() for p in prods if (p.get("unit") or "").strip()})
    for u in units:
        try:
            # UOM has no Qoyod id of its own; key naturally on uom_name.
            existing = frappe.db.exists("UOM", u)
            if existing:
                stats.skipped += 1
                continue
            if not commit:
                stats.created += 1
                continue
            doc = frappe.get_doc({"doctype": "UOM", "uom_name": u, "enabled": 1})
            doc.insert(ignore_permissions=True)
            stats.created += 1
        except Exception as e:  # noqa: BLE001
            stats.errors += 1
            log(f"  ! UOM {u!r}: {e}")
    log(stats.line("UOM"))
    return stats


def load_item_groups(commit, log):
    """Categories -> Item Group. Parents (parent_id null) before children."""
    stats = Stats()
    cats = _load("categories")
    cats.sort(key=lambda c: (c.get("parent_id") is not None, c.get("id")))  # roots first
    id_to_name = {}
    for c in cats:
        try:
            root_group = config.get_root_item_group()
            parent = root_group
            if c.get("parent_id") is not None:
                parent = id_to_name.get(c["parent_id"], root_group)
            vals = {
                "item_group_name": c["name"],
                "parent_item_group": parent,
                "is_group": 1,  # allow children to nest under it
            }
            name = upsert("Item Group", c["id"], vals, commit, stats,
                          natural_key="item_group_name", natural_val=c["name"])
            id_to_name[c["id"]] = c["name"]
        except Exception as e:  # noqa: BLE001
            stats.errors += 1
            log(f"  ! Item Group {c.get('name')!r}: {e}")
    log(stats.line("Item Group"))
    return stats, id_to_name


def _contact_common(q):
    """Shared Customer/Supplier field extraction."""
    has_tax = bool((q.get("tax_number") or "").strip())
    vals = {
        "tax_id": q.get("tax_number") or None,
        "mobile_no": q.get("phone_number") or None,
        "email_id": q.get("email") or None,
        "website": q.get("website") or None,
        "custom_secondary_phone": q.get("secondary_phone_number") or None,
        "custom_government_entity": 1 if q.get("government_entity") else 0,
    }
    billing = q.get("billing_address")
    if billing:
        vals["custom_qoyod_billing_json"] = json.dumps(billing, ensure_ascii=False)
    return vals, has_tax


def load_customers(commit, log):
    stats = Stats()
    for q in _load("customers"):
        try:
            common, has_tax = _contact_common(q)
            vals = {
                "customer_name": q.get("name"),
                "customer_type": "Company" if has_tax else "Individual",
                "customer_group": config.get_customer_group_company() if has_tax
                    else config.get_customer_group_individual(),
                "territory": config.get_territory(),
                **common,
                **_qoyod_values("Customer", q),  # fill Qoyod Data tab on insert
            }
            bank = q.get("bank")
            if bank:
                vals["custom_qoyod_bank_json"] = json.dumps(bank, ensure_ascii=False)
            upsert("Customer", q["id"], vals, commit, stats,
                   natural_key="customer_name", natural_val=q.get("name"))
        except Exception as e:  # noqa: BLE001
            stats.errors += 1
            log(f"  ! Customer id={q.get('id')} {q.get('name')!r}: {e}")
    log(stats.line("Customer"))
    return stats


def load_suppliers(commit, log):
    stats = Stats()
    for q in _load("vendors"):
        try:
            common, has_tax = _contact_common(q)
            common.pop("custom_qoyod_bank_json", None)
            vals = {
                "supplier_name": q.get("name"),
                "supplier_type": "Company" if has_tax else "Individual",
                "supplier_group": config.get_supplier_group(),
                **common,
                **_qoyod_values("Supplier", q),  # fill Qoyod Data tab on insert
            }
            upsert("Supplier", q["id"], vals, commit, stats,
                   natural_key="supplier_name", natural_val=q.get("name"))
        except Exception as e:  # noqa: BLE001
            stats.errors += 1
            log(f"  ! Supplier id={q.get('id')} {q.get('name')!r}: {e}")
    log(stats.line("Supplier"))
    return stats


def load_items(commit, log, cat_id_to_name):
    stats = Stats()
    prods = _load("products")
    for q in prods:
        try:
            item_group = cat_id_to_name.get(q.get("category_id"), config.get_root_item_group())
            uom = (q.get("unit") or "").strip() or "Nos"
            vals = {
                "item_code": q.get("sku") or q.get("name_en"),
                "item_name": q.get("name_en") or q.get("name_ar"),
                "item_group": item_group,
                "stock_uom": uom,
                "is_stock_item": 1 if q.get("track_quantity") else 0,
                "standard_rate": _to_float(q.get("selling_price")),
                "description": q.get("description") or None,
                "custom_name_ar": q.get("name_ar") or None,
                "custom_barcode": q.get("barcode") or None,
                "custom_buying_price": _to_float(q.get("buying_price")),
                "custom_qoyod_type": q.get("type") or None,
                **_qoyod_values("Item", q),  # fill Qoyod Data tab on insert
            }
            upsert("Item", q["id"], vals, commit, stats,
                   natural_key="item_code", natural_val=q.get("sku"))
        except Exception as e:  # noqa: BLE001
            stats.errors += 1
            log(f"  ! Item id={q.get('id')} {q.get('name_en')!r}: {e}")
    log(stats.line("Item"))
    return stats


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def main(commit=False):
    import frappe
    logs = []
    log = lambda s: (logs.append(s), print(s))[1]

    mode = "COMMIT (writing to DB)" if commit else "DRY RUN (no writes)"
    log(f"\n{'='*60}\nQoyod masters -> {config.get_company()}   [{mode}]\n{'='*60}")

    load_uoms(commit, log)
    _, cat_map = load_item_groups(commit, log)
    load_customers(commit, log)
    load_suppliers(commit, log)
    load_items(commit, log, cat_map)

    if commit:
        frappe.db.commit()
        log("\nCommitted.")
    else:
        log("\nDry run only — nothing written. Re-run with commit=True to apply.")
    return logs
