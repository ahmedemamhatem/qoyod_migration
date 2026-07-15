# Qoyod Migration

A standalone Frappe v15 app that migrates all data from the
[Qoyod](https://www.qoyod.com/) accounting API into ERPNext — masters,
addresses/contacts, the chart of accounts, and every transaction type —
**idempotently** (every record carries `custom_qoyod_id`, so re-runs never
duplicate).

Extracted from the earlier in-`grm_management` scripts into a clean, installable
app with a proper package layout, a Settings DocType for configuration (no
secrets in source), and a Sync Log.

## Install

```bash
bench --site <site> install-app qoyod_migration
bench --site <site> migrate          # applies custom fields + creates doctypes
```

Requires `erpnext`. `requests` is already provided by the bench env.

## Configure

Open **Qoyod Migration Settings** (Single) and set:

- **API Key** (encrypted Password) — the Qoyod `API-KEY`
- Company, Output VAT Account, and the Arabic default group names (sensible
  defaults are pre-filled)

Or headless: `bench --site <site> set-config qoyod_api_key '<KEY>'`.
Resolution order for every setting: **Settings → site_config → env → default**.

## Run

From the Settings form: **Test Connection**, **Dry Run**, **Run Full Sync**
(the full sync runs as a background job).

Headless:

```bash
# test the API
bench --site <site> execute qoyod_migration.qoyod_migration.api.test_connection

# dry run (connect + extract, validate, write nothing)
bench --site <site> execute qoyod_migration.qoyod_migration.api.run_full_sync

# full sync, reusing the shipped data dumps (no re-extract)
bench --site <site> execute qoyod_migration.qoyod_migration.api.run_full_sync \
    --kwargs "{'commit': True, 'extract': False}"

# full sync, pulling fresh data from Qoyod first
bench --site <site> execute qoyod_migration.qoyod_migration.api.run_full_sync \
    --kwargs "{'commit': True}"
```

## What it does — the 8-step sequence

1. **CONNECT** — verify the Qoyod API (HTTP 200)
2. **EXTRACT** — pull 14 endpoints → `data/*.json` (skippable with `extract=False`)
3. **SETUP** — create the Qoyod custom fields (id keys + read-only "Qoyod Data" sections)
4. **ACCOUNTS** — import the Qoyod chart as `<name>-Qoyod` / `<code>-Qoyod`
5. **MASTERS** — UOM → Item Group → Customer → Supplier → Item
6. **PROJECTS**, then **ADDRESSES/CONTACTS**, then company ZATCA + fiscal years + JE account-type fix
7. **TRANSACTIONS** — Quotations → Sales Invoices → Purchase Invoices → Credit Notes → Journal Entries → Payments; then link JEs to projects
8. **BACKFILL** — top-up the Qoyod Data fields (loaders already fill them on insert)

## Layout

```
qoyod_migration/qoyod_migration/
  config.py            # single source of truth (paths, api key, company, VAT, Arabic groups)
  orchestrator.py      # run() — the 8-step sequence, clean package imports
  api.py               # whitelisted: test_connection, run_full_sync, enqueue_full_sync
  custom_fields/       # base_fields, master_fields, txn_fields, misc_fields
  extract/extractor.py # Qoyod API -> data/*.json
  loaders/             # accounts, masters, addresses, projects, quotes,
                       # sales_invoices, purchase_invoices, credit_notes,
                       # journal_entries, payments
  data/                # shipped JSON dumps (reproducible offline)
  doctype/             # Qoyod Migration Settings (Single), Qoyod Sync Log (+ Step)
```

## Notes

- **Invoice numbering is NOT owned by this app.** Sales Invoices/Credit Notes are
  named after their Qoyod reference (INV###/CRN###) on insert, but the `autoname`
  rule that continues new invoices (INV218+) lives in `grm_management` and is left
  untouched. This app registers no `autoname` hook.
- Idempotent: keyed on `custom_qoyod_id` (accounts on `custom_qoyod_code`, credit
  notes on `CN-<id>`). Re-running on a loaded site creates ~nothing.
- Custom-field VALUES live in DB columns independent of definitions; installing
  this app alongside `grm_management` (which defines the same fields) is a safe
  idempotent no-op.
- The generated `data/qoyod_account_map.json` is a debug artifact — gitignored,
  not shipped, not read at runtime.

### License

mit
