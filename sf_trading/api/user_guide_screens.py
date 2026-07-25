# apps/sf_trading/sf_trading/api/user_guide_screens.py
"""Optional screenshots for the user guide.

Both guide surfaces — the desk page `sf-trading-user-guide` and the public `/user-guide` —
call `screens_markdown()` and append whatever it returns. It emits markdown only for image
files that actually exist, so:

  * before anyone captures a screen, the guide simply has no Screens section (no broken images)
  * the moment a PNG is dropped into sf_trading/public/images/guide/ with one of the expected
    names, both surfaces show it with its caption, no code change

Drop files at sf_trading/public/images/guide/<name>.png; Frappe serves them from
/assets/sf_trading/images/guide/<name>.png.
"""

import os

import frappe

FOLDER = os.path.join("public", "images", "guide")
ASSET_BASE = "/assets/sf_trading/images/guide"

# ordered: the flow a reader follows
EXPECTED = (
    ("payment-advice-form", "A Payment Advice with its references fetched, showing the allocation headline"),
    ("get-outstanding-dialog", "Get Outstanding Documents — the same filters as the Payment Entry form"),
    ("builder-preview", "Payment Advice Builder — suppliers grouped, with the skipped list and reasons"),
    ("builder-result", "The result panel after creating advices"),
    ("automation-settings", "Payment Automation Settings — the four steps and the plan sentence"),
    ("automation-dry-run", "A Dry Run summary: what the next scheduled run would do"),
    ("workflow-actions", "The PM Workflow actions on a submitted advice"),
    ("advice-list", "The Payment Advice list, colour-coded by status"),
)


def guide_image_dir():
    return os.path.join(frappe.get_app_path("sf_trading"), FOLDER)


def available_screens():
    """(filename, caption, url) for every expected screenshot that exists on disk."""
    directory = guide_image_dir()
    found = []
    for name, caption in EXPECTED:
        for extension in (".png", ".jpg", ".jpeg", ".webp"):
            path = os.path.join(directory, name + extension)
            if os.path.isfile(path):
                found.append((name + extension, caption, "%s/%s%s" % (ASSET_BASE, name, extension)))
                break
    return found


def missing_screens():
    present = {entry[0].rsplit(".", 1)[0] for entry in available_screens()}
    return [(name, caption) for name, caption in EXPECTED if name not in present]


def screens_markdown():
    """Markdown for the Screens section, or an empty string when no images exist yet."""
    screens = available_screens()
    if not screens:
        return ""

    lines = ["", "## 11. Screens", ""]
    for _filename, caption, url in screens:
        lines.append("### %s" % caption)
        lines.append("")
        lines.append("![%s](%s)" % (caption, url))
        lines.append("")

    return "\n".join(lines)
