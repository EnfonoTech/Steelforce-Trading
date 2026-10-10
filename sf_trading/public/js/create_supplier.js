/**
 * sf_trading: Create New Supplier dialog.
 * Registered on: Purchase Invoice, Purchase Order, Purchase Receipt.
 *
 * B2C (Individual) — Name + Mobile required only.
 * B2B (Company)    — Name + Mobile + VAT + CRN (optional) + Address block.
 *
 * Saudi Arabia companies:
 *   - VAT mandatory for B2B, exactly 15 digits, starts and ends with 3.
 *   - Full address (Line 1, Building No, District, City, Postal Code) mandatory for B2B.
 *   - Postal Code exactly 5 digits.
 * Non-Saudi companies:
 *   - VAT, address, postal code shown but not mandatory, no format enforced.
 */

["Purchase Invoice", "Purchase Order", "Purchase Receipt"].forEach(function (dt) {
    frappe.ui.form.on(dt, {
        refresh: function (frm) {
            sf_add_create_supplier_btn(frm);
        },
    });
});

function sf_add_create_supplier_btn(frm) {
    if (frm.doc.docstatus !== 0) return;
    if (!frm.fields_dict.supplier) return;

    var $field = frm.fields_dict.supplier.$wrapper;
    if ($field.parent().find(".sf-create-supplier-btn").length) return;

    var $btn = $(
        '<button type="button" class="btn btn-sm btn-secondary sf-create-supplier-btn"'
        + ' style="margin-bottom:5px;">'
        + '<i class="fa fa-plus"></i> ' + __("Create New Supplier")
        + "</button>"
    );
    $btn.on("click", function () {
        sf_open_create_supplier_dialog(frm);
    });
    $field.before($btn);
}

// the document's expiry is required when its Supporting Document Type validates expiry
function sf_party_dialog_expiry_rule(d) {
    var kind = d.get_value("document_type");
    if (!kind) {
        d.set_df_property("expiry_date", "reqd", 0);
        return;
    }
    frappe.db.get_value("Supporting Document Type", kind, "validate_expiry").then(function (r) {
        d.set_df_property("expiry_date", "reqd", r.message && r.message.validate_expiry ? 1 : 0);
    });
}

function sf_open_create_supplier_dialog(frm) {
    var company = frm.doc.company || frappe.defaults.get_default("company");

    var OVERRIDE_ROLES = ["Purchase Manager", "Purchase Master Manager", "System Manager"];
    var can_override = (frappe.user_roles || []).some(function (r) {
        return OVERRIDE_ROLES.indexOf(r) !== -1;
    });

    var can_b2b = (frappe.user_roles || []).some(function (r) {
        return ["B2B Creator", "System Manager", "Administrator"].indexOf(r) !== -1;
    });

    frappe.db.get_value("Company", company, ["country", "default_currency"], function (r) {
        var company_country = (r && r.country) || "";
        var is_saudi = company_country === "Saudi Arabia";

        var b2b = "eval:doc.buyer_kind === 'B2B (Company)'";
        var b2b_saudi = is_saudi ? b2b : "";

        var buyer_kind_field = can_b2b
            ? {
                fieldname: "buyer_kind",
                fieldtype: "Select",
                label: __("Supplier Kind"),
                options: "B2C (Individual)\nB2B (Company)",
                default: "B2B (Company)",
                reqd: 1,
                description: __("Every supplier: Name, Mobile, Email and a document. B2B: also Tax ID, CR and address details."),
            }
            : {
                fieldname: "buyer_kind",
                fieldtype: "Data",
                label: __("Supplier Kind"),
                default: "B2C (Individual)",
                hidden: 1,
                read_only: 1,
            };

        var d = new frappe.ui.Dialog({
            title: __("Create New Supplier"),
            size: "large",
            fields: [
                buyer_kind_field,
                { fieldtype: "Section Break" },
                {
                    fieldname: "supplier_name",
                    fieldtype: "Data",
                    label: __("Supplier Name"),
                    reqd: 1,
                },
                {
                    fieldname: "mobile_no",
                    fieldtype: "Data",
                    label: __("Mobile No"),
                    reqd: 1,
                },
                {
                    fieldname: "email_id",
                    fieldtype: "Data",
                    options: "Email",
                    label: __("Email ID"),
                    // no buying document can be raised for a supplier without one
                    reqd: 1,
                },
                // mandatory for a Company (B2B) supplier, foreign ones their own country's number,
                // optional for an Individual (sf_trading/supplier_validation.py) -- so it sits
                // outside the B2B section
                {
                    fieldname: "tax_id",
                    fieldtype: "Data",
                    label: __("Tax ID / VAT Registration Number"),
                    mandatory_depends_on: b2b,
                    description: is_saudi
                        ? __("Exactly 15 digits, starting and ending with 3.")
                        : __("Foreign suppliers: their own country's tax number."),
                },
                // any file, type not checked -- every supplier needs one (supplier_validation.py)
                {
                    fieldname: "attachment",
                    fieldtype: "Attach",
                    label: __("Document Attachment"),
                    reqd: 1,
                },
                {
                    fieldname: "payment_terms",
                    fieldtype: "Link",
                    options: "Payment Terms Template",
                    label: __("Payment Terms"),
                    reqd: 1,
                },
                {
                    fieldname: "document_type",
                    fieldtype: "Link",
                    options: "Supporting Document Type",
                    label: __("Document Name"),
                    default: "Commercial Registration",
                    
                    get_query: () => ({ filters: { disabled: 0, applies_to: ["in", ["Customer and Supplier", "Supplier"]] } }),
                    onchange() {
                        sf_party_dialog_expiry_rule(d);
                    },
                },
                {
                    fieldname: "document_number",
                    fieldtype: "Data",
                    label: __("Document #"),
                    
                },
                {
                    fieldname: "expiry_date",
                    fieldtype: "Date",
                    label: __("Expiry Date"),
                    
                },
                {
                    fieldname: "allow_duplicate_vat",
                    fieldtype: "Check",
                    label: __("Allow Duplicate VAT (Manager Override)"),
                    default: 0,
                    hidden: can_override ? 0 : 1,
                    depends_on: "eval:doc.tax_id",
                },
                {
                    fieldname: "duplicate_vat_reason",
                    fieldtype: "Small Text",
                    label: __("Duplicate VAT Reason"),
                    hidden: can_override ? 0 : 1,
                    depends_on: "eval:doc.allow_duplicate_vat",
                    mandatory_depends_on: "eval:doc.allow_duplicate_vat",
                },
                // ── B2B Details ──────────────────────────────────────────────
                {
                    fieldtype: "Section Break",
                    label: __("B2B Details"),
                    depends_on: b2b,
                },
                {
                    fieldname: "commercial_registration_number",
                    fieldtype: "Data",
                    label: __("Commercial Registration Number"),
                    depends_on: b2b,
                },
                // ── Address Details ──────────────────────────────────────────
                {
                    fieldtype: "Section Break",
                    label: __("Address Details"),
                    depends_on: b2b,
                },
                {
                    fieldname: "address_type",
                    fieldtype: "Select",
                    label: __("Address Type"),
                    options: "Billing\nShipping",
                    default: "Billing",
                    depends_on: b2b,
                    mandatory_depends_on: b2b_saudi,
                },
                {
                    fieldname: "address_line1",
                    fieldtype: "Data",
                    label: __("Address Line 1"),
                    depends_on: b2b,
                    mandatory_depends_on: b2b_saudi,
                },
                {
                    fieldname: "address_line2",
                    fieldtype: "Data",
                    label: __("Address Line 2"),
                    depends_on: b2b,
                },
                {
                    fieldname: "custom_building_number",
                    fieldtype: "Data",
                    label: __("Building Number"),
                    depends_on: b2b,
                    mandatory_depends_on: b2b_saudi,
                },
                {
                    fieldname: "district",
                    fieldtype: "Data",
                    label: __("District / Area"),
                    depends_on: b2b,
                    mandatory_depends_on: b2b_saudi,
                },
                {
                    fieldname: "city",
                    fieldtype: "Data",
                    label: __("City"),
                    depends_on: b2b,
                    mandatory_depends_on: b2b_saudi,
                },
                {
                    fieldname: "country",
                    fieldtype: "Link",
                    options: "Country",
                    label: __("Country"),
                    default: company_country,
                    depends_on: b2b,
                    mandatory_depends_on: b2b_saudi,
                },
                {
                    fieldname: "pincode",
                    fieldtype: "Data",
                    label: __("Postal Code"),
                    depends_on: b2b,
                    mandatory_depends_on: b2b_saudi,
                    description: is_saudi ? __("Exactly 5 digits.") : "",
                },
            ],

            primary_action_label: __("Create Supplier"),
            primary_action: function (values) {
                var is_b2b = values.buyer_kind === "B2B (Company)";
                var allow_dup = values.allow_duplicate_vat ? 1 : 0;
                var dup_reason = (values.duplicate_vat_reason || "").trim();
                var vat = (values.tax_id || "").trim();

                if (is_saudi && (values.mobile_no || "").replace(/\D/g, "").length < 10) {
                    frappe.msgprint(__("Mobile number must have at least 10 digits."));
                    return;
                }
                if (allow_dup && !can_override) {
                    frappe.msgprint(__("You do not have permission to override the VAT duplicate check."));
                    return;
                }
                if (allow_dup && !dup_reason) {
                    frappe.msgprint(__("Please provide a reason for allowing duplicate VAT."));
                    return;
                }

                if (is_b2b && !vat) {
                    frappe.msgprint(__("Tax ID is required for B2B (Company) suppliers."));
                    return;
                }
                if (is_b2b && is_saudi) {
                    if (!/^3\d{13}3$/.test(vat)) {
                        frappe.msgprint(__("VAT must be exactly 15 digits, starting and ending with 3."));
                        return;
                    }
                    if ((values.pincode || "").replace(/\D/g, "").length !== 5) {
                        frappe.msgprint(__("Postal Code must be exactly 5 digits."));
                        return;
                    }
                }

                // VAT duplicate pre-check
                if (vat && !allow_dup) {
                    frappe.db
                        .get_value("Supplier", { tax_id: vat }, "name")
                        .then(function (res) {
                            if (res.message && res.message.name) {
                                frappe.msgprint(
                                    __("VAT already used by Supplier: {0}.", [res.message.name])
                                    + (can_override
                                        ? " " + __("Tick 'Allow Duplicate VAT' to override.")
                                        : "")
                                );
                                return;
                            }
                            do_create();
                        });
                    return;
                }

                do_create();

                function do_create() {
                    frappe.call({
                        method: "sf_trading.api.supplier.create_supplier_with_address",
                        args: {
                            supplier_name:                values.supplier_name,
                            mobile_no:                    values.mobile_no,
                            email_id:                     values.email_id || null,
                            buyer_kind:                   values.buyer_kind,
                            company:                      company,
                            country:                      (is_b2b ? values.country : null) || company_country,
                            tax_id:                       vat || null,
                            commercial_registration_number: is_b2b ? (values.commercial_registration_number || null) : null,
                            address_type:                 is_b2b ? (values.address_type || null) : null,
                            address_line1:                is_b2b ? (values.address_line1 || null) : null,
                            address_line2:                is_b2b ? (values.address_line2 || null) : null,
                            custom_building_number:       is_b2b ? (values.custom_building_number || null) : null,
                            district:                     is_b2b ? (values.district || null) : null,
                            city:                         is_b2b ? (values.city || null) : null,
                            pincode:                      is_b2b ? (values.pincode || null) : null,
                            allow_duplicate_vat:          allow_dup,
                            duplicate_vat_reason:         allow_dup ? dup_reason : null,
                            attachment:                   values.attachment,
                            payment_terms:                values.payment_terms,
                            document_type:                values.document_type || null,
                            document_number:              values.document_number || null,
                            expiry_date:                  values.expiry_date || null,
                        },
                        callback: function (r) {
                            if (r.message) {
                                frm.set_value("supplier", r.message.supplier);
                                frm.refresh_field("supplier");
                                frappe.show_alert({ message: r.message.message, indicator: "green" });
                                d.hide();
                            }
                        },
                    });
                }
            },
        });

        d.show();
        sf_party_dialog_expiry_rule(d);
        if (company_country) {
            d.set_value("country", company_country);
        }

        // Digit masks
        d.fields_dict.mobile_no.$input.on("input", function () {
            this.value = this.value.replace(/[^0-9]/g, "").slice(0, 15);
        });
        if (is_saudi) {
            d.fields_dict.tax_id.$input.on("input", function () {
                this.value = this.value.replace(/[^0-9]/g, "").slice(0, 15);
            });
            d.fields_dict.pincode.$input.on("input", function () {
                this.value = this.value.replace(/[^0-9]/g, "").slice(0, 5);
            });
        }
    });
}
