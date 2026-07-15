"""
Import ALL Qoyod accounts into the ERPNext chart of accounts, suffixed.
======================================================================

  * Import every Qoyod account (no match/skip -- every account is created).
  * account_name   -> "<name>{suffix}"      (suffix from config, default -Qoyod)
  * account_number -> "<code>{suffix}"
  * Tagged with custom_qoyod_id and custom_qoyod_code (raw code) for the
    transaction-phase lookup.

Tree built (mirrors Qoyod's own categorisation):
    <root group of RootType>
      └─ "Qoyod Imported - <RootType>"          (group)
           └─ "<group_type>{suffix}"           (group, per Qoyod group_type)
                └─ "<name>{suffix}"            (ledger leaf)

The suffix keeps the imported chart distinct from the site's native accounts, so
this is safe to run against a company that already has its own chart of accounts.
Idempotent: re-running skips accounts already tagged with the same
custom_qoyod_code. DRY RUN by default.
"""

import json
import os

from qoyod_migration.qoyod_migration import config
from qoyod_migration.qoyod_migration.config import data_dir

MAP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qoyod_account_map.json")


def _qoyod_values(record):
    """Fill the Account Qoyod Data tab fields on insert (shared with backfill)."""
    try:
        from qoyod_migration.qoyod_migration.custom_fields import master_fields as mf
        return mf.qoyod_values("Account", record)
    except Exception:  # noqa: BLE001
        return {}

QROOT = {"Asset": "Asset", "Liability": "Liability", "Equity": "Equity",
         "Revenue": "Income", "Expense": "Expense"}

# Qoyod group_type -> ERPNext account_type (only high-confidence ones).
GROUPTYPE_ATYPE = {
    "Property, plant, and equipment": "Fixed Asset",
    "Accumulated Depreciation": "Accumulated Depreciation",
    "Depreciation": "Depreciation",
    "Accounts payable": "Payable",
    "Accounts receivable": "Receivable",
    "Petty cash": "Cash",
    "Cost of sales": "Cost of Goods Sold",
    "Prepaid expenses and others": "Current Asset",
    "Other Current Liability": "Current Liability",
    "Employees advances": "Current Asset",
}


def _atype(q):
    gt = q.get("group_type") or ""
    if gt in GROUPTYPE_ATYPE:
        return GROUPTYPE_ATYPE[gt]
    nm = (q.get("name_en") or "").lower() + " " + (q.get("name_ar") or "")
    if "bank" in gt.lower() or "بنك" in nm:
        return "Bank"
    if "tax" in gt.lower() or "ضريب" in nm:
        return "Tax"
    return None


def build(commit=False):
    import frappe

    company = config.get_company()
    suffix = config.get_account_suffix()

    # find an existing root-type group to hang each holding group under
    root_parent = {}
    for rt in set(QROOT.values()):
        g = frappe.db.get_value("Account",
            {"company": company, "root_type": rt, "is_group": 1, "parent_account": ["in", ["", None]]},
            "name")
        if not g:
            g = frappe.db.get_value("Account",
                {"company": company, "root_type": rt, "is_group": 1}, "name")
        root_parent[rt] = g

    with open(os.path.join(data_dir(), "accounts.json"), encoding="utf-8") as f:
        qaccts = json.load(f)

    # group accounts by (root, group_type)
    groups = {}
    for q in qaccts:
        root = QROOT.get(q.get("type"))
        gt = q.get("group_type") or "Uncategorized"
        groups.setdefault((root, gt), []).append(q)

    print("=" * 60)
    print(f"Import ALL Qoyod accounts -> {company}   [{'COMMIT' if commit else 'DRY RUN'}]")
    print("=" * 60)
    print(f"  total accounts : {len(qaccts)}")
    print(f"  holding groups : {len(set(r for r,_ in groups))} roots, "
          f"{len(groups)} group_type sub-groups")
    typed = sum(1 for q in qaccts if _atype(q))
    print(f"  with mapped account_type: {typed}/{len(qaccts)}")

    mapping = {}
    created_groups = created_leaves = skipped = errors = 0

    def ensure_group(account_name, parent, root):
        """Create-or-get a group account. Returns its name."""
        nonlocal created_groups
        existing = frappe.db.get_value("Account",
            {"company": company, "account_name": account_name, "is_group": 1}, "name")
        if existing:
            return existing
        if not commit:
            created_groups += 1
            return f"(grp {account_name})"
        doc = frappe.get_doc({
            "doctype": "Account", "account_name": account_name,
            "parent_account": parent, "root_type": root,
            "is_group": 1, "company": company,
        })
        doc.insert(ignore_permissions=True)
        created_groups += 1
        return doc.name

    for (root, gt), accts in groups.items():
        if not root or not root_parent.get(root):
            errors += len(accts)
            print(f"  ! no parent for root={root!r} (group_type={gt}) -> {len(accts)} skipped")
            continue
        holding = ensure_group(f"Qoyod Imported - {root}", root_parent[root], root)
        subgroup = ensure_group(f"{gt}{suffix}", holding, root)

        for q in accts:
            code = str(q["code"])
            try:
                existing = frappe.db.get_value("Account",
                    {"company": company, "custom_qoyod_code": code}, "name")
                if existing:
                    skipped += 1
                    mapping[code] = {"qoyod_id": q["id"], "qoyod_code": code,
                        "qoyod_name": q.get("name_ar"), "erpnext_account": existing,
                        "match": "exists"}
                    continue
                acct_name = f"{q.get('name_ar') or q.get('name_en')}{suffix}"
                acct_number = f"{code}{suffix}"
                if not commit:
                    created_leaves += 1
                    mapping[code] = {"qoyod_id": q["id"], "qoyod_code": code,
                        "qoyod_name": q.get("name_ar"),
                        "erpnext_account": f"(create) {acct_number} - {acct_name}",
                        "account_type": _atype(q), "root_type": root, "match": "to_create"}
                    continue
                doc = frappe.get_doc({
                    "doctype": "Account",
                    "account_name": acct_name,
                    "account_number": acct_number,
                    "parent_account": subgroup,
                    "root_type": root,
                    "account_type": _atype(q) or None,
                    "is_group": 0,
                    "company": company,
                    "custom_qoyod_id": str(q["id"]),
                    "custom_qoyod_code": code,
                    **_qoyod_values(q),  # fill Qoyod Data tab on insert
                })
                doc.insert(ignore_permissions=True)
                created_leaves += 1
                mapping[code] = {"qoyod_id": q["id"], "qoyod_code": code,
                    "qoyod_name": q.get("name_ar"), "erpnext_account": doc.name,
                    "match": "created"}
            except Exception as e:  # noqa: BLE001
                errors += 1
                print(f"  ! account code={code} {q.get('name_ar')!r}: {e}")

    if commit:
        frappe.db.commit()

    print(f"\n  group accounts   created: {created_groups}")
    print(f"  ledger accounts  created: {created_leaves}")
    print(f"  skipped (exists) : {skipped}")
    print(f"  errors           : {errors}")

    with open(MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)
    print(f"  map ({len(mapping)}) -> {MAP_FILE}")
    print("  " + ("COMMITTED." if commit else "DRY RUN — nothing written."))
    return mapping
