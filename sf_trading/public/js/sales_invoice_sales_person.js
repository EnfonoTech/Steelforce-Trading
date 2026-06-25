// Auto-populate Sales Person and sales_team from Customer master
frappe.ui.form.on("Sales Invoice", {
	customer: function (frm) {
		if (!frm.doc.customer) return;

		frm.set_value("custom_sales_person", "");
		frm.clear_table("sales_team");
		frm.refresh_field("sales_team");

		frappe.db.get_doc("Customer", frm.doc.customer).then(function (doc) {
			if (doc.sales_team && doc.sales_team.length) {
				frm.set_value("custom_sales_person", doc.sales_team[0].sales_person);

				doc.sales_team.forEach(function (d) {
					const row = frm.add_child("sales_team");
					row.sales_person = d.sales_person;
					row.allocated_percentage = d.allocated_percentage || 100;
				});

				frm.refresh_field("sales_team");
			}
		});
	},

	custom_sales_person: function (frm) {
		if (!frm.doc.custom_sales_person) return;

		frm.clear_table("sales_team");

		const row = frm.add_child("sales_team");
		row.sales_person = frm.doc.custom_sales_person;
		row.allocated_percentage = 100;

		frm.refresh_field("sales_team");
	},
});
