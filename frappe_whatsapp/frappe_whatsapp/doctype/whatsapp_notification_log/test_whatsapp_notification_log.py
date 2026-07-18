# Copyright (c) 2022, Shridhar Patil and Contributors
# See license.txt

import frappe
from frappe.utils import add_days
from frappe_whatsapp.testing import IntegrationTestCase


class TestWhatsAppNotificationLog(IntegrationTestCase):
    """Tests for WhatsApp Notification Log doctype."""

    def tearDown(self):
        for name in frappe.get_all("WhatsApp Notification Log", filters={"template": ["like", "Test Log%"]}, pluck="name"):
            frappe.delete_doc("WhatsApp Notification Log", name, force=True)
        frappe.db.commit()  # nosemgrep: frappe-manual-commit -- test fixture must be visible to later queries

    def test_log_creation(self):
        """Test basic notification log creation."""
        doc = frappe.get_doc({
            "doctype": "WhatsApp Notification Log",
            "template": "Test Log Template",
            "meta_data": '{"status": "success"}'
        })
        doc.insert(ignore_permissions=True)
        self.assertTrue(frappe.db.exists("WhatsApp Notification Log", doc.name))

    def test_log_with_json_metadata(self):
        """Test log stores JSON metadata correctly."""
        import json
        meta = {"messages": [{"id": "wamid.123"}], "contacts": [{"wa_id": "919900112233"}]}
        doc = frappe.get_doc({
            "doctype": "WhatsApp Notification Log",
            "template": "Test Log JSON",
            "meta_data": json.dumps(meta)
        })
        doc.insert(ignore_permissions=True)
        doc.reload()

        stored_meta = json.loads(doc.meta_data)
        self.assertEqual(stored_meta["messages"][0]["id"], "wamid.123")

    def test_log_with_error_metadata(self):
        """Test log stores error metadata."""
        import json
        meta = {"error": "Failed to send message: Invalid phone number"}
        doc = frappe.get_doc({
            "doctype": "WhatsApp Notification Log",
            "template": "Test Log Error",
            "meta_data": json.dumps(meta)
        })
        doc.insert(ignore_permissions=True)
        doc.reload()

        stored_meta = json.loads(doc.meta_data)
        self.assertIn("error", stored_meta)

    def test_clear_old_logs_purges_old_keeps_recent(self):
        """clear_old_logs(30) deletes rows older than 30 days and keeps recent ones."""
        from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_notification_log.whatsapp_notification_log import (
            WhatsAppNotificationLog,
        )

        # Insert a stale row (31 days ago)
        old_doc = frappe.get_doc({
            "doctype": "WhatsApp Notification Log",
            "template": "Test Log Retention Old",
            "meta_data": "{}",
        })
        old_doc.insert(ignore_permissions=True)
        frappe.db.set_value(
            "WhatsApp Notification Log", old_doc.name, "creation", add_days(frappe.utils.now(), -31)
        )

        # Insert a recent row (today)
        recent_doc = frappe.get_doc({
            "doctype": "WhatsApp Notification Log",
            "template": "Test Log Retention Recent",
            "meta_data": "{}",
        })
        recent_doc.insert(ignore_permissions=True)
        frappe.db.commit()  # nosemgrep: frappe-manual-commit -- test fixture must be visible to later queries

        WhatsAppNotificationLog.clear_old_logs(days=30)
        frappe.db.commit()  # nosemgrep: frappe-manual-commit -- flush purge before asserts

        self.assertFalse(frappe.db.exists("WhatsApp Notification Log", old_doc.name), "old row must be purged")
        self.assertTrue(frappe.db.exists("WhatsApp Notification Log", recent_doc.name), "recent row must survive")
