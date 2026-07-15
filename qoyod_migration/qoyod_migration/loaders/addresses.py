"""
Qoyod addresses + contacts -> ERPNext Address / Contact
======================================================

Creates proper linked ERPNext records (not JSON blobs) from Qoyod customer &
vendor data:

  billing_address (real)  -> Address (Billing),  linked to Customer/Supplier
  shipping_address (real) -> Address (Shipping)
  phone/email             -> Contact,            linked to Customer/Supplier

Idempotent: an Address/Contact carries custom_qoyod_id (source contact id +
suffix) so re-runs update instead of duplicating.
Bank data is all-empty in the source, so it is skipped.
"""

import json
import os

from qoyod_migration.qoyod_migration import config
from qoyod_migration.qoyod_migration.config import data_dir


def _s(v):
    return (str(v).strip() if v is not None else "")


def _has(d, keys):
    return bool(d) and any(_s(d.get(k)) for k in keys)


# Qoyod sometimes stores country in Arabic; ERPNext Country names are English.
COUNTRY_MAP = {
    "السعودية": "Saudi Arabia",
    "المملكة العربية السعودية": "Saudi Arabia",
    "": "Saudi Arabia",
}


def _country(v):
    v = _s(v)
    return COUNTRY_MAP.get(v, v or "Saudi Arabia")


def build(commit=False):
    import frappe

    def party_name(dt, qid):
        return frappe.db.get_value(dt, {"custom_qoyod_id": str(qid)}, "name")

    plans = [
        ("customers", "Customer"),
        ("vendors", "Supplier"),
    ]

    addr_created = addr_skip = contact_created = contact_skip = errors = 0
    err_samples = []

    for src, party_dt in plans:
        recs = json.load(open(os.path.join(data_dir(), f"{src}.json"), encoding="utf-8"))
        for r in recs:
            party = party_name(party_dt, r.get("id"))
            if not party:
                continue

            # ---- Billing / Shipping Address ----
            for kind, block, akeys in [
                ("Billing", r.get("billing_address"),
                 ["billing_address", "billing_city", "billing_state", "billing_zip"]),
                ("Shipping", r.get("shipping_address"),
                 ["shipping_address", "shipping_city", "shipping_state", "shipping_zip"]),
            ]:
                if not _has(block, akeys):
                    continue
                pfx = "billing" if kind == "Billing" else "shipping"
                tag = f"{r['id']}-{party_dt[:3].lower()}-{kind.lower()}"
                title = f"{r.get('name','')[:60]} ({kind})"
                line1 = _s(block.get(f"{pfx}_address")) or _s(block.get("building_number")) or title
                vals = {
                    "doctype": "Address",
                    "address_title": r.get("name") or title,
                    "address_type": kind,
                    "address_line1": line1,
                    "address_line2": _s(block.get("building_number")),
                    "city": _s(block.get(f"{pfx}_city")) or None,
                    "state": _s(block.get(f"{pfx}_state")) or None,
                    "pincode": _s(block.get(f"{pfx}_zip")) or None,
                    "country": _country(block.get(f"{pfx}_country")),
                    "custom_qoyod_id": tag,
                    "links": [{"link_doctype": party_dt, "link_name": party}],
                }
                try:
                    existing = frappe.db.get_value("Address", {"custom_qoyod_id": tag}, "name")
                    if existing:
                        addr_skip += 1
                        continue
                    if commit:
                        frappe.get_doc(vals).insert(ignore_permissions=True)
                    addr_created += 1
                except Exception as e:  # noqa: BLE001
                    errors += 1
                    if len(err_samples) < 10:
                        err_samples.append(f"addr {tag}: {str(e)[:120]}")

            # ---- Contact (phone / email) ----
            phone = _s(r.get("phone_number"))
            phone2 = _s(r.get("secondary_phone_number"))
            email = _s(r.get("email"))
            if phone or phone2 or email:
                tag = f"{r['id']}-{party_dt[:3].lower()}-contact"
                nm = _s(r.get("name")) or f"Contact {r['id']}"
                cvals = {
                    "doctype": "Contact",
                    "first_name": nm[:140],
                    "custom_qoyod_id": tag,
                    "links": [{"link_doctype": party_dt, "link_name": party}],
                }
                if email:
                    cvals["email_ids"] = [{"email_id": email, "is_primary": 1}]
                phones = []
                if phone:
                    phones.append({"phone": phone, "is_primary_phone": 1})
                if phone2:
                    phones.append({"phone": phone2})
                if phones:
                    cvals["phone_nos"] = phones
                try:
                    existing = frappe.db.get_value("Contact", {"custom_qoyod_id": tag}, "name")
                    if existing:
                        contact_skip += 1
                    else:
                        if commit:
                            frappe.get_doc(cvals).insert(ignore_permissions=True)
                        contact_created += 1
                except Exception as e:  # noqa: BLE001
                    errors += 1
                    if len(err_samples) < 10:
                        err_samples.append(f"contact {tag}: {str(e)[:120]}")

    if commit:
        frappe.db.commit()

    print("=" * 56)
    print(f"Qoyod Address/Contact import  [{'COMMIT' if commit else 'DRY RUN'}]")
    print("=" * 56)
    print(f"  Addresses created: {addr_created}  skipped: {addr_skip}")
    print(f"  Contacts  created: {contact_created}  skipped: {contact_skip}")
    print(f"  errors: {errors}")
    for s in err_samples:
        print("   ", s)
    return {"addr": addr_created, "contact": contact_created, "errors": errors}
