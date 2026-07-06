frappe.ui.form.on("Stock Entry", {
    setup: function (frm) {
        frm._wh_cleared = false;
    },
    refresh: function (frm) {
        if (frm.doc.__islocal && !frm._wh_cleared) {
            frm._wh_cleared = true;

            // For a Material Transfer mapped from a Material Request, rows already
            // carry the correct s_warehouse/t_warehouse — keep the header default
            // clearing for every other purpose/flow, but backfill the header here
            // instead of blanking it.
            if (frm.doc.purpose === "Material Transfer" && (frm.doc.items || []).length) {
                const unique_values = function (field) {
                    const values = [...new Set((frm.doc.items || []).map(row => row[field]).filter(Boolean))];
                    return values.length === 1 ? values[0] : null;
                };

                if (!frm.doc.from_warehouse) {
                    const s_warehouse = unique_values("s_warehouse");
                    if (s_warehouse) frm.set_value("from_warehouse", s_warehouse);
                }
                if (!frm.doc.to_warehouse) {
                    const t_warehouse = unique_values("t_warehouse");
                    if (t_warehouse) frm.set_value("to_warehouse", t_warehouse);
                }
                return;
            }

            frm.set_value("from_warehouse", "");
            frm.set_value("to_warehouse", "");
        }
    },
});
