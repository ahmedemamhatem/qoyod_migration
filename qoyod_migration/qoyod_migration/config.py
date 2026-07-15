# Copyright (c) 2026, Ahmed Emam and contributors
# For license information, please see license.txt
"""
Single source of truth for the Qoyod migration.

Everything configurable (API key/base, target company, currency, VAT account and
rate, default groups/territory, the account-name suffix) resolves through here,
in order:

    Qoyod Migration Settings (Single DocType)  ->  site_config.json  ->  env  ->  default

Where a sensible value can be discovered from the site itself (root Item Group,
root Territory, a leaf Customer/Supplier group, the company currency, a Tax
account), the resolver auto-detects it instead of assuming a hard-coded name --
so the app works on any ERPNext site, English or Arabic, without configuration.

The extracted JSON dumps live in this package's ``data/`` folder, resolved via
frappe.get_app_path (robust regardless of cwd).
"""

import json
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


# --------------------------------------------------------------------------
# Settings access
# --------------------------------------------------------------------------

SETTINGS = "Qoyod Migration Settings"

# Defaults. The Arabic group names below match a KSA ERPNext site created with
# an Arabic chart of accounts; override them in Qoyod Migration Settings for any
# other tenant (English installs, different group naming, non-SAR currency, etc.).
DEFAULT_API_BASE = "https://api.qoyod.com/2.0"
DEFAULT_ROOT_ITEM_GROUP = "All Item Groups"
DEFAULT_TERRITORY = "All Territories"
DEFAULT_CG_COMPANY = "Commercial"
DEFAULT_CG_INDIVIDUAL = "Individual"
DEFAULT_SUPPLIER_GROUP = "All Supplier Groups"
DEFAULT_ACCOUNT_SUFFIX = "-Qoyod"
DEFAULT_VAT_ACCOUNT_NAME = "VAT"
DEFAULT_VAT_RATE = 15.0


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
	"""Output/input-VAT account. Resolution:
	Settings.vat_account -> leaf Account whose name contains the default VAT name
	-> any leaf Account with account_type 'Tax' on the company. Returns None if
	the company has no tax account (loaders then post without a VAT row)."""
	acc = _setting("vat_account")
	if acc:
		return acc
	company = get_company()
	acc = frappe.db.get_value(
		"Account",
		{"company": company, "account_name": ["like", f"%{DEFAULT_VAT_ACCOUNT_NAME}%"], "is_group": 0},
		"name")
	if not acc:
		acc = frappe.db.get_value(
			"Account", {"company": company, "account_type": "Tax", "is_group": 0}, "name")
	return acc


def get_vat_rate():
	"""Standard VAT/GST percentage applied to taxed lines (KSA default 15%)."""
	val = _setting("vat_rate")
	if val in (None, ""):
		return DEFAULT_VAT_RATE
	try:
		return float(val)
	except (TypeError, ValueError):
		return DEFAULT_VAT_RATE


def get_currency():
	"""Transaction currency. Settings.currency -> the target company's currency."""
	cur = _setting("currency")
	if cur:
		return cur
	return frappe.db.get_value("Company", get_company(), "default_currency")


def get_root_item_group():
	"""Root Item Group new categories hang under. Settings -> configured default
	-> the site's actual is_group root ('All Item Groups' on English installs)."""
	val = _setting("root_item_group")
	if val:
		return val
	root = frappe.db.get_value(
		"Item Group", {"is_group": 1, "parent_item_group": ["in", ["", None]]}, "name")
	return root or DEFAULT_ROOT_ITEM_GROUP


def get_territory():
	val = _setting("default_territory")
	if val:
		return val
	root = frappe.db.get_value(
		"Territory", {"is_group": 1, "parent_territory": ["in", ["", None]]}, "name")
	return root or DEFAULT_TERRITORY


def get_customer_group_company():
	return _setting("customer_group_company") or _default_customer_group()


def get_customer_group_individual():
	return _setting("customer_group_individual") or _default_customer_group()


def _default_customer_group():
	"""A safe existing Customer Group: an explicit non-group leaf, else the root."""
	grp = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
	if grp:
		return grp
	return frappe.db.get_value(
		"Customer Group", {"is_group": 1, "parent_customer_group": ["in", ["", None]]}, "name"
	) or DEFAULT_CG_COMPANY


def get_supplier_group():
	val = _setting("default_supplier_group")
	if val:
		return val
	grp = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
	if grp:
		return grp
	return frappe.db.get_value(
		"Supplier Group", {"is_group": 1, "parent_supplier_group": ["in", ["", None]]}, "name"
	) or DEFAULT_SUPPLIER_GROUP


def get_account_suffix():
	return _setting("account_suffix") or DEFAULT_ACCOUNT_SUFFIX


# --------------------------------------------------------------------------
# Small shared helpers used across loaders
# --------------------------------------------------------------------------


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
