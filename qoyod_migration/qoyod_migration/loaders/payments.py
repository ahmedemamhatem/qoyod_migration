"""
Qoyod receipts -> ERPNext Payment Entries
=========================================

  kind == "received"  -> Receive (from Customer)
  kind == "paid"      -> Pay (to Supplier)
  account_id          -> bank/cash Account (paid_to for Receive, paid_from for Pay)
  allocations[]       -> references against imported Sales/Purchase Invoices
                         (allocatee_type Invoice -> Sales Invoice,
                          allocatee_type Bill    -> Purchase Invoice;
                          matched via allocatee_id -> custom_qoyod_id)

Receipts with no allocations become on-account payments.
Idempotent (Payment Entry.custom_qoyod_id). Submitted. DRY RUN by default.
"""

import json
import os

from qoyod_migration.qoyod_migration import config
from qoyod_migration.qoyod_migration.config import data_dir


def _qoyod_values(fskey, record):
	"""Fill the Qoyod Data section fields on insert (shared with backfill)."""
	try:
		from qoyod_migration.qoyod_migration.custom_fields import txn_fields as tf
		return tf.qoyod_values(fskey, record)
	except Exception:  # noqa: BLE001
		return {}



def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def build(commit=False, limit=None):
    import frappe

    company = config.get_company()
    cost_center = frappe.db.get_value("Company", company, "cost_center")
    recv_acc = frappe.db.get_value("Company", company, "default_receivable_account")
    pay_acc = frappe.db.get_value("Company", company, "default_payable_account")

    def acc(qid):
        return frappe.db.get_value("Account", {"custom_qoyod_id": str(qid)}, "name")

    def si_for(qid):
        return frappe.db.get_value("Sales Invoice",
            {"custom_qoyod_id": str(qid), "docstatus": 1},
            ["name", "grand_total", "outstanding_amount"], as_dict=True)

    def pi_for(qid):
        return frappe.db.get_value("Purchase Invoice",
            {"custom_qoyod_id": str(qid), "docstatus": 1},
            ["name", "grand_total", "outstanding_amount"], as_dict=True)

    receipts = json.load(open(os.path.join(data_dir(), "receipts.json"), encoding="utf-8"))
    if limit:
        receipts = receipts[:limit]

    created = skipped = errors = 0
    err_samples = []
    print("=" * 60)
    print(f"Qoyod receipts -> Payment Entry  "
          f"[{'COMMIT' if commit else 'DRY RUN'}]  n={len(receipts)}")
    print("=" * 60)

    for q in receipts:
        qid = str(q["id"])
        try:
            if frappe.db.get_value("Payment Entry", {"custom_qoyod_id": qid}, "name"):
                skipped += 1
                continue

            kind = q.get("kind")
            received = (kind == "received")
            party_type = "Customer" if received else "Supplier"
            party = frappe.db.get_value(
                party_type, {"custom_qoyod_id": str(q.get("contact_id"))}, "name")
            if not party:
                raise ValueError(f"{party_type} contact_id={q.get('contact_id')} not found")

            bank = acc(q.get("account_id"))
            if not bank:
                raise ValueError(f"bank account_id={q.get('account_id')} not found")

            amount = _f(q.get("amount"))
            pe = frappe.new_doc("Payment Entry")
            pe.payment_type = "Receive" if received else "Pay"
            pe.company = company
            pe.posting_date = q.get("date")
            pe.party_type = party_type
            pe.party = party
            pe.custom_qoyod_id = qid
            pe.reference_no = q.get("reference")
            pe.reference_date = q.get("date")
            pe.remarks = q.get("description") or None
            # Fill the Qoyod Data section on insert.
            for _fn, _v in _qoyod_values("Payment Entry", q).items():
                pe.set(_fn, _v)
            if received:
                pe.paid_to = bank
                pe.received_amount = amount
                pe.paid_amount = amount
                pe.paid_from = recv_acc
            else:
                pe.paid_from = bank
                pe.paid_amount = amount
                pe.received_amount = amount
                pe.paid_to = pay_acc

            # allocations -> references
            for a in q.get("allocations", []):
                at = a.get("allocatee_type")
                alloc_amt = _f(a.get("amount"))
                if at == "Invoice":
                    ref = si_for(a.get("allocatee_id"))
                    dt = "Sales Invoice"
                elif at == "Bill":
                    ref = pi_for(a.get("allocatee_id"))
                    dt = "Purchase Invoice"
                else:
                    continue
                if not ref:
                    continue  # target invoice not imported; leave on-account
                # Clamp to the invoice's current outstanding (a prior payment or
                # credit note may already have reduced it). abs() because purchase
                # outstanding is stored positive too.
                outstanding = abs(_f(ref.get("outstanding_amount")))
                alloc = min(alloc_amt, outstanding) if outstanding else 0
                if alloc <= 0:
                    continue
                pe.append("references", {
                    "reference_doctype": dt,
                    "reference_name": ref.name,
                    "total_amount": ref.grand_total,
                    "allocated_amount": alloc,
                })

            if commit:
                pe.insert(ignore_permissions=True)
                pe.submit()
                created += 1
            else:
                pe.set_missing_values()
                created += 1

        except Exception as e:  # noqa: BLE001
            errors += 1
            if len(err_samples) < 15:
                err_samples.append(f"receipt id={qid}: {str(e)[:180]}")

    if commit:
        frappe.db.commit()
    print(f"\n  created: {created}  skipped: {skipped}  errors: {errors}")
    for s in err_samples:
        print("   ", s)
    print("  " + ("COMMITTED." if commit else "DRY RUN — nothing written."))
    return {"created": created, "skipped": skipped, "errors": errors}
