# Qoyod → ERPNext Migration

A standalone **Frappe v15 / ERPNext v15** app that migrates a complete
[Qoyod](https://www.qoyod.com/) accounting account into ERPNext — the chart of
accounts, all master data, addresses & contacts, and every transaction type —
**idempotently** and **losslessly**.

- **Idempotent** — every imported record carries a `custom_qoyod_id` (accounts
  also carry `custom_qoyod_code`). The loaders find-or-create on that key, so you
  can re-run the sync as many times as you like without creating duplicates.
- **Lossless** — every Qoyod field that has no natural home in ERPNext is kept in
  a read-only **“Qoyod Data”** tab/section on the target document, so nothing is
  silently dropped.
- **Configuration, not code** — the API key, target company, currency, VAT
  account/rate, and default groups all resolve through a Settings DocType (or
  `site_config.json` / environment variables). Where a value can be discovered
  from the site itself, it is auto-detected. **No secrets and no site-specific
  names live in the source.**

> This app was extracted from a production KSA (Saudi Arabia) migration, but it is
> written to run against **any** ERPNext site — English or Arabic, SAR or any
> other currency, VAT 15% or any other rate.

---

## Contents

- [How it works (ELT)](#how-it-works-elt)
- [Requirements](#requirements)
- [Install](#install)
- [Configure](#configure)
- [Run](#run)
- [The 8-step pipeline](#the-8-step-pipeline)
- [What maps to what](#what-maps-to-what)
- [Idempotency & re-runs](#idempotency--re-runs)
- [Sync Log](#sync-log)
- [Adapting to your tenant](#adapting-to-your-tenant)
- [Data privacy](#data-privacy)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)
- [License](#license)

---

## How it works (ELT)

The migration is an **Extract → Load → Transform** pipeline:

1. **Extract** — pull every readable Qoyod 2.0 endpoint into raw JSON files under
   the app’s `data/` folder (one file per resource). This is a faithful dump; no
   mapping, no transformation. You can re-run the load offline without hitting the
   API again.
2. **Load / Transform** — read those JSON dumps and create ERPNext documents via
   the **Frappe ORM** (never raw SQL), in strict dependency order, submitting
   transactions so the GL posts.

Running the two halves separately (`extract` vs. `commit`) means you can extract
once and iterate on the load safely.

---

## Requirements

| | |
|---|---|
| Frappe | v15 |
| ERPNext | v15 (declared as a `required_app`) |
| Python | ≥ 3.10 |
| A Qoyod account | with an API key that can read the resources you want to import |

`requests` is already provided by the bench environment.

---

## Install

```bash
# from your bench directory
bench get-app https://github.com/ahmedemamhatem/qoyod_migration.git
bench --site <site> install-app qoyod_migration
bench --site <site> migrate          # creates the doctypes + custom fields
```

Custom fields are (re)created automatically on every `install` and `migrate`
(idempotent), and again at the start of every commit run — so they are always in
sync with the code.

---

## Configure

Open **Qoyod Migration Settings** (a Single DocType) and set:

| Field | Required? | Notes |
|-------|-----------|-------|
| **API Key** | ✅ | Qoyod `API-KEY` header value. Stored **encrypted** (Password field). |
| **API Base URL** | — | Defaults to `https://api.qoyod.com/2.0`. |
| **Company** | — | Target ERPNext Company. Empty ⇒ the first company on the site. |
| **Currency** | — | Empty ⇒ the target company’s default currency. |
| **VAT Account** | — | Output/input VAT ledger. Empty ⇒ a leaf account whose name contains “VAT”, else any `Tax`-type leaf. |
| **Default VAT Rate (%)** | — | Fallback rate for taxed lines. **Each line’s own `tax_percent` wins when present.** Default `15`. |
| **Root Item Group / Territory / Customer & Supplier Groups** | — | Empty ⇒ auto-detected from the site (root group / leaf group / root territory). |
| **Account Suffix** | — | Appended to imported account names/numbers (default `-Qoyod`) so they never collide with your native chart. |

Every setting resolves in this order:

```
Qoyod Migration Settings  →  site_config.json  →  environment variable  →  default / auto-detect
```

**Headless configuration** (no UI):

```bash
bench --site <site> set-config qoyod_api_key  '<KEY>'
bench --site <site> set-config qoyod_api_base 'https://api.qoyod.com/2.0'
# or export QOYOD_API_KEY / QOYOD_API_BASE in the environment
```

---

## Run

### From the Settings form

Three buttons:

- **Test Connection** — verifies the API key/base reach Qoyod (HTTP 200).
- **Dry Run** — connect + extract + validate, **writes nothing**.
- **Run Full Sync (Commit)** — runs the whole pipeline as a **background job** and
  posts to ERPNext. Progress and results are recorded in a **Qoyod Sync Log**.

### Headless (bench execute)

```bash
# 1) Test the API
bench --site <site> execute qoyod_migration.qoyod_migration.api.test_connection

# 2) Dry run (connect + extract, validate, write nothing)
bench --site <site> execute qoyod_migration.qoyod_migration.api.run_full_sync

# 3) Full sync, pulling fresh data from Qoyod first
bench --site <site> execute qoyod_migration.qoyod_migration.api.run_full_sync \
    --kwargs "{'commit': True}"

# 4) Full sync, reusing the dumps already in data/ (no re-extract)
bench --site <site> execute qoyod_migration.qoyod_migration.api.run_full_sync \
    --kwargs "{'commit': True, 'extract': False}"

# 5) Full sync but skip the transaction phase (masters/accounts only)
bench --site <site> execute qoyod_migration.qoyod_migration.api.run_full_sync \
    --kwargs "{'commit': True, 'skip_transactions': True}"
```

> **Recommended first run:** a **Dry Run**, then inspect the console output /
> Sync Log, then commit.

`run_full_sync` is idempotent — safe to re-run if a batch partly fails.

---

## The 8-step pipeline

Everything runs in strict dependency order, so a later step can always resolve
the records an earlier step created.

| # | Step | Creates / does |
|---|------|----------------|
| 1 | **Connect** | Verify the Qoyod API (HTTP 200). |
| 2 | **Extract** | Pull every endpoint → `data/*.json` (skippable with `extract=False`). |
| 3 | **Setup** | Create the Qoyod custom fields (id keys + read-only “Qoyod Data” sections). |
| 4 | **Accounts** | Import the Qoyod chart as suffixed group/leaf accounts under your existing roots. |
| 5 | **Masters** | UOM → Item Group → Customer → Supplier → Item. |
| 5b | **Projects** | Qoyod projects → ERPNext Projects. |
| 6 | **Addresses / Contacts** | Real linked Address & Contact records for each party. Then prerequisites: company ZATCA fields (from invoice QR), fiscal years, and a party-type fix so party-less JE lines post. |
| 7 | **Transactions** | Quotations → Sales Invoices → Purchase Invoices → Credit Notes → Journal Entries → Payments; then links JEs to their projects. |
| 8 | **Backfill** | Top-up the “Qoyod Data” fields (loaders already fill them on insert; this repairs anything created earlier). |

---

## What maps to what

| Qoyod resource | ERPNext target | Key resolution |
|----------------|----------------|----------------|
| `accounts` | **Account** (suffixed) | `custom_qoyod_code` |
| `categories` | **Item Group** | `custom_qoyod_id` |
| `product_unit_types` / product `unit` | **UOM** | UOM name |
| `products` | **Item** | `custom_qoyod_id` / SKU |
| `customers` | **Customer** (+ Address, Contact) | `custom_qoyod_id` |
| `vendors` | **Supplier** (+ Address, Contact) | `custom_qoyod_id` |
| `projects` | **Project** | `custom_qoyod_id` |
| `quotes` | **Quotation** | `custom_qoyod_id` |
| `invoices` | **Sales Invoice** (submitted) | `custom_qoyod_id` |
| `bills` | **Purchase Invoice** (submitted) | `custom_qoyod_id` |
| `credit_notes` | **Sales Invoice** `is_return=1` (submitted) | `CN-<id>` |
| `journal_entries` | **Journal Entry** (submitted) | `custom_qoyod_id` |
| `receipts` | **Payment Entry** (submitted) | `custom_qoyod_id` |

**Pricing rules** used by the invoice/quote/credit-note loaders:

- The Qoyod line discount is **baked into the rate** so the net reconciles exactly
  (avoids ERPNext’s per-unit discount ambiguity).
- Tax-inclusive lines set `included_in_print_rate` on the tax row.
- A VAT row is added **only when a line actually carries tax**, at the **line’s own
  rate** (falling back to the configured default). Zero-rated / exempt lines get no
  VAT row.
- Payments allocate against imported invoices, **clamped to each invoice’s current
  outstanding**; anything left over stays on-account.

---

## Idempotency & re-runs

- Every loader checks for an existing record by its Qoyod key before creating one,
  so **re-running a completed sync creates ≈ nothing**.
- Master upserts also match on a natural key (name / SKU / code) so they **adopt**
  pre-existing records instead of erroring on a duplicate.
- Custom-field **values** live in DB columns independent of the field
  **definitions**, so re-creating the definitions never disturbs imported data.

---

## Sync Log

Every commit run creates a **Qoyod Sync Log** record with:

- **Status** — Running → Success / Partial (some rows errored) / Failed.
- **Mode / flags** — Dry Run vs. Commit, whether it extracted, who triggered it.
- **Per-step results** — created / skipped / errors for each transaction step.
- **Console output** and, on failure, the **full traceback**.

Use the log to confirm counts and to find the exact records that errored.

---

## Adapting to your tenant

This app has **no site-specific names baked in**. To point it at your own data:

1. Set your **API Key** and **Company** in Settings.
2. Leave the group/territory/VAT fields **empty** to auto-detect, or set them
   explicitly to your own records.
3. If your tax rate isn’t 15%, set **Default VAT Rate** (or rely on the per-line
   `tax_percent` from Qoyod, which always wins).
4. If you want the imported chart under a different name convention, change the
   **Account Suffix**.

The extractor’s `RESOURCES` map (in `extract/extractor.py`) lists the endpoints it
pulls. If your Qoyod plan exposes more/fewer resources, edit that map.

---

## Data privacy

The extracted `data/*.json` dumps contain **real financial records and personal
data** (customer/vendor names, emails, phone numbers, tax numbers, transactions).
They are **git-ignored** and must never be committed to a public repository. The
generated `data/qoyod_account_map.json` is a local debug artifact and is likewise
ignored. Treat the `data/` folder as confidential.

---

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `Qoyod API key missing` | Set it in Settings, `site_config.json`, or `QOYOD_API_KEY`. |
| `extract=False but no data/*.json dumps are present` | Run once with `extract=True` (default) to pull the data first. |
| `No Company found on this site` | Create a Company (ERPNext setup) before running. |
| Transactions error with “account is a Receivable/Payable” | The pipeline auto-clears that account type on imported accounts before posting JEs; make sure step 6 ran (it does automatically on commit). |
| Sales Invoice submit blocked by ZATCA | The pipeline backfills the company’s Arabic name + VAT number from an invoice’s ZATCA QR; ensure your invoices carry `qrcode_string`, or set those company fields manually. |
| Some rows show under **errors** in the Sync Log | Open the log’s console output — each error line names the offending Qoyod id and the reason (e.g. an unmapped item/party). |

---

## Project layout

```
qoyod_migration/qoyod_migration/
  config.py            # single source of truth: paths, API key, company, currency,
                       #   VAT account/rate, groups/territory (Settings → config → env → auto-detect)
  orchestrator.py      # run() — the 8-step pipeline; writes a Qoyod Sync Log; restores the session user
  api.py               # whitelisted: test_connection, run_full_sync, enqueue_full_sync
  extract/extractor.py # Qoyod API → data/*.json (paginated, retrying, manifest)
  custom_fields/       # base_fields (id keys), master_fields + txn_fields (Qoyod Data tabs), misc_fields
  loaders/             # accounts, masters, addresses, projects, quotes,
                       #   sales_invoices, purchase_invoices, credit_notes,
                       #   journal_entries, payments
  data/                # extracted JSON dumps (git-ignored — confidential)
  doctype/             # Qoyod Migration Settings (Single), Qoyod Sync Log (+ Step)
install.py             # after_install / after_migrate → (re)create custom fields
hooks.py               # required_apps=[erpnext], after_install/after_migrate
```

---

## License

MIT — see [`license.txt`](license.txt).
