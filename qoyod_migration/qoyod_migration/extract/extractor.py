"""
Qoyod full extractor -> JSON files
==================================

Pulls EVERYTHING we can read from the Qoyod 2.0 API into raw JSON files, one
file per resource, into the package's data/ folder. This is the "extract" half
of an ELT pipeline: no mapping, no transformation, just faithful raw dumps we can
re-process offline without re-hitting the API.

Only endpoints that return 200 on this account are in RESOURCES. Two pagination
styles are handled: products returns an explicit `pagination` object; everything
else returns a bare list, and some endpoints (journal_entries, receipts) IGNORE
per_page and dump the whole resource in one page -- we detect a page larger than
PAGE_SIZE and stop, else it would loop forever.

The API key + base come from config (Qoyod Migration Settings / site_config / env);
never from source.
"""

import json
import os
import time
from datetime import datetime, timezone

import requests

from qoyod_migration.qoyod_migration import config

PAGE_SIZE = 100
REQUEST_PAUSE = 0.2          # polite pacing between pages
SLOW_TIMEOUT = 120           # journal_entries and friends are slow
FAST_TIMEOUT = 60
MAX_PAGES = 10000            # hard stop against an endpoint that never short-pages

# resource path -> the JSON key its list lives under (None => same as path)
RESOURCES = {
    "customers": "customers",
    "vendors": "vendors",
    "categories": "categories",
    "inventories": "inventories",
    "accounts": "accounts",
    "products": "products",
    "product_unit_types": "product_unit_types",
    "projects": "projects",
    "quotes": "quote",   # NOTE: quotes endpoint returns its list under "quote" (singular)
    "invoices": "invoices",
    "credit_notes": "credit_notes",
    "bills": "bills",
    "receipts": "receipts",
    "journal_entries": "journal_entries",
}

# resources known to be slow -> use the longer timeout
SLOW = {"journal_entries", "invoices", "bills", "receipts", "credit_notes"}


def make_session(key):
    s = requests.Session()
    s.headers.update({"API-KEY": key, "Accept": "application/json"})
    return s


def fetch_all(session, resource, listkey, base=None):
    """Return (records, meta) for one resource, following all pages."""
    base = base or config.get_api_base()
    records = []
    timeout = SLOW_TIMEOUT if resource in SLOW else FAST_TIMEOUT
    page = 1
    pagination = None
    while True:
        params = {"page": page, "per_page": PAGE_SIZE}
        # retry a page a couple of times on transient timeouts
        for attempt in range(3):
            try:
                resp = session.get(f"{base}/{resource}", params=params, timeout=timeout)
                break
            except requests.exceptions.RequestException as e:
                if attempt == 2:
                    raise
                print(f"    (retry {resource} page {page} after {type(e).__name__})")
                time.sleep(2 + attempt * 2)
        resp.raise_for_status()
        data = resp.json()

        items = data.get(listkey) or data.get("data") or []
        pagination = data.get("pagination") or pagination

        # Decide whether to continue.
        if pagination and str(pagination.get("totalPages", "1")).isdigit():
            # Endpoint supports real paging (e.g. products).
            total_pages = int(pagination["totalPages"])
            records.extend(items)
            print(f"    page {page}/{total_pages} (+{len(items)}, running {len(records)})")
            if page >= total_pages or not items:
                break
        elif len(items) > PAGE_SIZE:
            # Endpoint IGNORES per_page and dumped the whole resource in one
            # page (e.g. journal_entries returns ~1680 at once). Take it and
            # stop -- paging further just re-returns the same rows forever.
            records.extend(items)
            print(f"    single-dump page {page} (+{len(items)}; endpoint ignores per_page, stopping)")
            break
        else:
            # Standard short-page detection.
            records.extend(items)
            print(f"    page {page} (+{len(items)}, running {len(records)})")
            if len(items) < PAGE_SIZE:
                break
        page += 1
        if page > MAX_PAGES:
            print(f"    !! {resource}: hit MAX_PAGES={MAX_PAGES}, stopping to avoid an infinite loop")
            break
        time.sleep(REQUEST_PAUSE)
    return records, pagination


def main(api_key=None, base=None, out_dir=None):
    """Extract every resource to out_dir (default: package data/). Key/base come
    from config unless passed explicitly. Returns the summary dict."""
    api_key = api_key or config.get_api_key()
    base = base or config.get_api_base()
    out_dir = out_dir or config.data_dir()
    os.makedirs(out_dir, exist_ok=True)
    session = make_session(api_key)

    summary = {}
    for resource, listkey in RESOURCES.items():
        print(f"\n== {resource} ==")
        try:
            records, pagination = fetch_all(session, resource, listkey, base=base)
        except Exception as e:  # noqa: BLE001
            print(f"  !! FAILED {resource}: {type(e).__name__}: {e}")
            summary[resource] = {"error": f"{type(e).__name__}: {e}"}
            continue

        out_path = os.path.join(out_dir, f"{resource}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        print(f"  wrote {len(records)} records -> {out_path}")
        summary[resource] = {"count": len(records), "file": f"{resource}.json"}
        if pagination:
            summary[resource]["reported_total"] = pagination.get("total")

    manifest = {
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "base": base,
        "resources": summary,
    }
    with open(os.path.join(out_dir, "_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print("EXTRACTION SUMMARY")
    print("=" * 60)
    for r, info in summary.items():
        if "error" in info:
            print(f"  {r:18} ERROR: {info['error']}")
        else:
            extra = ""
            if info.get("reported_total") not in (None, info.get("count")):
                extra = f"  (API reported total={info['reported_total']})"
            print(f"  {r:18} {info['count']:>6} records{extra}")
    print(f"\nAll files in: {out_dir}")
    return summary
