frappe.ui.form.on("Stock Entry", {
    setup: function (frm) {
        frm._wh_cleared = false;
    },
    refresh: function (frm) {
        if (frm.doc.__islocal && !frm._wh_cleared) {
            frm._wh_cleared = true;
            frm.set_value("from_warehouse", "");
            frm.set_value("to_warehouse", "");
        }
    },
});
