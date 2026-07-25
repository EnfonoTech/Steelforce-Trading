# apps/sf_trading/sf_trading/www/user-guide.py
"""Public user guide at /user-guide.

Renders `sf_trading/docs/USER_GUIDE.md` — the same file the desk page
`sf-trading-user-guide` shows — so the two surfaces can never drift apart. No login required.
"""

import os

import frappe

no_cache = 1

TOC_HEADING = "## Table of Contents"


def get_context(context):
    context.title = "SF Trading User Guide"
    context.guide_html = render_guide()
    return context


def strip_manual_toc(markdown):
    """Drop the hand-written table of contents.

    It exists so the file reads well on GitHub; both rendered surfaces build their own index
    from the headings, so keeping it would print the list twice.
    """
    if TOC_HEADING not in markdown:
        return markdown

    before, _, rest = markdown.partition(TOC_HEADING)
    next_heading = rest.find("\n## ")
    return before + (rest[next_heading:] if next_heading != -1 else "")


def render_guide():
    path = os.path.join(frappe.get_app_source_path("sf_trading"), "docs", "USER_GUIDE.md")
    if not os.path.isfile(path):
        return "<p>%s</p>" % frappe._("The user guide file is missing on this server.")

    with open(path, encoding="utf-8") as handle:
        markdown = handle.read()

    return frappe.utils.md_to_html(strip_manual_toc(markdown))
