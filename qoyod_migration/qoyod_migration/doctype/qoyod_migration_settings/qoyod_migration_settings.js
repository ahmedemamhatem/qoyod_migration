// Copyright (c) 2026, Ahmed Emam and contributors
// For license information, please see license.txt

frappe.ui.form.on("Qoyod Migration Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Test Connection"), () => {
			frm.call("test_connection").then((r) => {
				if (r.message) frappe.msgprint(r.message);
			});
		});

		frm.add_custom_button(__("Dry Run"), () => {
			frappe.confirm(__("Run a dry-run sync (connect + extract, no writes)?"), () => {
				frm.call("run_sync", { commit: 0, extract: 1 }).then((r) => {
					if (r.message) frappe.msgprint(r.message);
				});
			});
		});

		frm.add_custom_button(__("Run Full Sync (Commit)"), () => {
			frappe.confirm(
				__("Run the FULL sync and WRITE to ERPNext? This posts GL entries."),
				() => {
					frm.call("run_sync", { commit: 1, extract: 0 }).then((r) => {
						if (r.message) frappe.msgprint(r.message);
					});
				}
			);
		}).addClass("btn-primary");
	},
});
