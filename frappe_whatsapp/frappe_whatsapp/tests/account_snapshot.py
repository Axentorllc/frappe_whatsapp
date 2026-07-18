# Copyright (c) 2025, Shridhar Patil and Contributors
# See license.txt

"""Shared helpers for snapshotting and restoring WhatsApp Account default flags
across test class boundaries.

Usage in setUpClass / tearDownClass:

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._acct_snap = snapshot_defaults()
        # ... create fixtures ...

    @classmethod
    def tearDownClass(cls):
        # ... delete fixtures ...
        restore_defaults(cls._acct_snap)
        super().tearDownClass()
"""

import frappe


def snapshot_defaults():
    """Capture (name, is_default_incoming, is_default_outgoing) for every account.

    Returns a list of dicts. Call before creating any test fixtures.
    """
    return frappe.db.get_all(
        "WhatsApp Account",
        fields=["name", "is_default_incoming", "is_default_outgoing"],
    )


def restore_defaults(snapshot):
    """Re-apply captured flags to every snapshotted account that still exists.

    Uses db.set_value (no on_update cascade) to write back both flags atomically
    per account, then commits once.
    """
    for row in snapshot:
        if frappe.db.exists("WhatsApp Account", row["name"]):
            frappe.db.set_value(
                "WhatsApp Account",
                row["name"],
                {
                    "is_default_incoming": row["is_default_incoming"],
                    "is_default_outgoing": row["is_default_outgoing"],
                },
            )
    frappe.db.commit()  # nosemgrep: frappe-manual-commit -- test fixture restore
