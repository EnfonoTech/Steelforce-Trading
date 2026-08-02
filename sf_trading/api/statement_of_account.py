# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Header details printed on a Customer Statement of Account.

The statement layout carries a customer block (name, IDs, address, terms,
credit limit) and Steel Force's letterhead artwork. None of that comes out of
the ledger, so the report's client script pulls it from here into hidden
filters, which the print template then reads.
"""

import re

import frappe
from frappe import _
from frappe.contacts.doctype.address.address import get_address_display, get_default_address
from frappe.utils import cstr

from erpnext.selling.doctype.customer.customer import get_credit_limit

IMG_SRC = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.IGNORECASE)


@frappe.whitelist()
def get_statement_header(customer, company):
    """Return the statement header as hidden-filter values.

    Keys match the hidden filters declared in customer_statement_of_account.js;
    the print template reads them off `filters`.
    """
    frappe.has_permission("Customer", "read", doc=customer, throw=True)

    doc = frappe.get_cached_doc("Customer", customer)
    header_image, footer_image = get_letter_head_images(company)

    return {
        "soa_customer_name": doc.customer_name or doc.name,
        "soa_customer_name_ar": doc.get("customer_name_in_arabic")
        or doc.get("custom_customer_name_arabic")
        or "",
        "soa_customer_id": doc.name,
        "soa_address": get_customer_address(customer),
        "soa_vat_no": doc.tax_id or doc.get("custom_vat_registration_number") or "",
        "soa_cr_no": doc.get("custom_commercial_registration_number") or "",
        "soa_payment_terms": doc.payment_terms or "",
        "soa_credit_limit": get_credit_limit(customer, company) or 0,
        "soa_collector": get_collector(customer),
        "soa_company_name": company,
        "soa_header_image": header_image,
        "soa_footer_image": footer_image,
    }


def get_customer_address(customer):
    """Primary address as one line, mirroring what ERPNext prints elsewhere."""
    address = get_default_address("Customer", customer)
    if not address:
        return ""

    display = get_address_display(frappe.get_cached_doc("Address", address).as_dict()) or ""
    return cstr(display).replace("<br>", ", ").strip(", ")


def get_collector(customer):
    """The sales person on the customer — the statement prints them as collector."""
    return (
        frappe.db.get_value(
            "Sales Team",
            {"parent": customer, "parenttype": "Customer"},
            "sales_person",
            order_by="allocated_percentage desc",
        )
        or ""
    )


def get_letter_head_images(company):
    """Header and footer artwork from the company's letter head, if it has any.

    Steel Force's letter head is a pair of images rather than markup, so the
    statement reuses them instead of rebuilding the branding in the template.
    """
    letter_head = frappe.db.get_value(
        "Letter Head", {"disabled": 0, "is_default": 1}, ["content", "footer"], as_dict=True
    )
    if not letter_head:
        return "", ""

    return first_image(letter_head.content), first_image(letter_head.footer)


def first_image(html):
    """First <img> source, made absolute.

    wkhtmltopdf renders the header and footer as documents of their own, with
    no site as their base, so a site-relative src would silently come out blank.
    """
    match = IMG_SRC.search(cstr(html))
    if not match:
        return ""

    src = match.group(1)
    return frappe.utils.get_url(src) if src.startswith("/") else src
