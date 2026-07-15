# Copyright (c) 2026, Ahmed Emam and contributors
# For license information, please see license.txt
"""
Single source of truth for the Qoyod migration.

Everything configurable (API key/base, target company, VAT account, Arabic
default groups, the -Qoyod account suffix) resolves through here, in order:

    Qoyod Migration Settings (Single DocType)  ->  site_config.json  ->  env  ->  default

The extracted JSON dumps live in this package's ``data/`` folder, resolved via
frappe.get_app_path (robust regardless of cwd) -- no symlinks, no per-module
DATA_DIR rewriting.
"""

import os

import frappe

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

APP = "qoyod_migration"
MODULE = "qoyod_migration"


def data_dir():
	"""Absolute path to the shipped/extracted JSON dumps folder."""
	path = frappe.get_app_path(APP, MODULE, "data")
	os.makedirs(path, exist_ok=True)
	return path


# Convenience constant (evaluated lazily by callers that import it).
DATA_DIR = None  # set on first access via data_dir(); kept for readability only


# --------------------------------------------------------------------------
# Settings access
# --------------------------------------------------------------------------

SETTINGS = "Qoyod Migration Settings"

# Defaults (site "uat.grm.sa" / company قرم conventions).
DEFAULT_API_BASE = "https://api.qoyod.com/2.0"
DEFAULT_ROOT_ITEM_GROUP = "كل مجموعات الأصناف"
DEFAULT_TERRITORY = "جميع الأقاليم"
DEFAULT_CG_COMPANY = "تجاري"
DEFAULT_CG_INDIVIDUAL = "فرد"
DEFAULT_SUPPLIER_GROUP = "جميع مجموعات الموردين"
DEFAULT_ACCOUNT_SUFFIX = "-Qoyod"
DEFAULT_VAT_ACCOUNT_NAME = "VAT 15%"


def _settings():
	"""Return the Settings Single, or None if the doctype isn't installed yet."""
	if not frappe.db.exists("DocType", SETTINGS):
		return None
	try:
		return frappe.get_cached_doc(SETTINGS)
	except Exception:  # noqa: BLE001
		return None


def _setting(fieldname, default=None):
	s = _settings()
	if s:
		val = s.get(fieldname)
		if val not in (None, ""):
			return val
	return default


# --------------------------------------------------------------------------
# Resolvers
# --------------------------------------------------------------------------

def get_api_base():
	return _setting("api_base") or frappe.conf.get("qoyod_api_base") \
		or os.environ.get("QOYOD_API_BASE") or DEFAULT_API_BASE


def get_api_key():
	"""Qoyod API key: Settings (encrypted Password) -> site_config -> env. Throws if missing."""
	s = _settings()
	if s:
		try:
			pw = s.get_password("api_key", raise_exception=False)
			if pw:
				return pw
		except Exception:  # noqa: BLE001
			pass
	key = frappe.conf.get("qoyod_api_key") or os.environ.get("QOYOD_API_KEY")
	if not key:
		frappe.throw(
			"Qoyod API key missing. Set it in Qoyod Migration Settings, "
			"or `qoyod_api_key` in site_config.json, or the QOYOD_API_KEY env var."
		)
	return key


def get_company():
	"""Target company: Settings.company -> first Company on the site."""
	company = _setting("company")
	if company:
		return company
	names = frappe.get_all("Company", pluck="name")
	if not names:
		frappe.throw("No Company found on this site.")
	return names[0]


def get_vat_account():
	"""Output-VAT account: Settings.vat_account -> Account named 'VAT 15%' (leaf)."""
	acc = _setting("vat_account")
	if acc:
		return acc
	company = get_company()
	acc = frappe.db.get_value(
		"Account", {"company": company, "account_name": DEFAULT_VAT_ACCOUNT_NAME, "is_group": 0}, "name")
	if not acc:
		acc = frappe.db.get_value(
			"Account", {"company": company, "account_type": "Tax", "is_group": 0}, "name")
	return acc


def get_root_item_group():
	return _setting("root_item_group", DEFAULT_ROOT_ITEM_GROUP)


def get_territory():
	return _setting("default_territory", DEFAULT_TERRITORY)


def get_customer_group_company():
	return _setting("customer_group_company", DEFAULT_CG_COMPANY)


def get_customer_group_individual():
	return _setting("customer_group_individual", DEFAULT_CG_INDIVIDUAL)


def get_supplier_group():
	return _setting("default_supplier_group", DEFAULT_SUPPLIER_GROUP)


def get_account_suffix():
	return _setting("account_suffix", DEFAULT_ACCOUNT_SUFFIX)


# --------------------------------------------------------------------------
# Small shared helpers used across loaders
# --------------------------------------------------------------------------

import json  # noqa: E402


def load_json(name):
	with open(os.path.join(data_dir(), f"{name}.json"), encoding="utf-8") as f:
		return json.load(f)


def dump_json(name, data):
	with open(os.path.join(data_dir(), f"{name}.json"), "w", encoding="utf-8") as f:
		json.dump(data, f, ensure_ascii=False, indent=2)


def has_dump(name):
	return os.path.exists(os.path.join(data_dir(), f"{name}.json"))


def to_float(v):
	try:
		return float(v)
	except (TypeError, ValueError):
		return 0.0
