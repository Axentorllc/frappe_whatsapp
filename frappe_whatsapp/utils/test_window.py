# Copyright (c) 2025, Shridhar Patil and Contributors
# See license.txt

from datetime import datetime, timedelta

import frappe
from frappe_whatsapp.testing import IntegrationTestCase


class TestWindowPrimitive(IntegrationTestCase):
    """Feature 7: 24h window primitive edge cases."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not frappe.db.exists("WhatsApp Account", "Test WA Window Account"):
            acc = frappe.get_doc({
                "doctype": "WhatsApp Account",
                "account_name": "Test WA Window Account",
                "status": "Active",
                "url": "https://graph.facebook.com",
                "version": "v17.0",
                "phone_id": "window_test_phone_id",
                "business_id": "window_biz_id",
                "app_id": "window_app_id",
                "webhook_verify_token": "window_verify_token",
                "is_default_incoming": 0,
                "is_default_outgoing": 0,
            })
            acc.insert(ignore_permissions=True)
            frappe.db.commit()  # nosemgrep: frappe-manual-commit -- test fixture must be visible to later queries

    def _create_inbound(self, from_number, created_at):
        """Insert a minimal Incoming message with a specific creation timestamp."""
        msg = frappe.get_doc({
            "doctype": "WhatsApp Message",
            "type": "Incoming",
            "from": from_number,
            "message": "Window test",
            "message_id": f"wamid.WINDOW.{frappe.generate_hash(length=8)}",
            "content_type": "text",
            "whatsapp_account": "Test WA Window Account",
        })
        msg.flags.ignore_validate = True
        msg.db_insert()
        frappe.db.set_value(
            "WhatsApp Message", msg.name, "creation", created_at, update_modified=False
        )
        frappe.db.commit()  # nosemgrep: frappe-manual-commit -- test fixture must be visible to later queries
        return msg

    def tearDown(self):
        frappe.db.sql(
            "DELETE FROM `tabWhatsApp Message` WHERE message_id LIKE 'wamid.WINDOW%'"
        )
        frappe.db.commit()  # nosemgrep: frappe-manual-commit -- test fixture must be visible to later queries

    def test_no_inbound_not_allowed(self):
        from frappe_whatsapp.utils.window import can_send_session_message
        result = can_send_session_message("201000099999")
        self.assertFalse(result["allowed"])
        self.assertIsNone(result["window_expires_at"])
        self.assertIsNone(result["last_inbound_at"])

    def test_within_24h_allowed(self):
        from frappe_whatsapp.utils.window import can_send_session_message
        # 23h59m ago — inside window
        ts = datetime.utcnow() - timedelta(hours=23, minutes=59)
        self._create_inbound("201000000002", ts)
        result = can_send_session_message("201000000002")
        self.assertTrue(result["allowed"])
        self.assertIsNotNone(result["window_expires_at"])

    def test_beyond_24h_not_allowed(self):
        from frappe_whatsapp.utils.window import can_send_session_message
        # 24h01m ago — outside window
        ts = datetime.utcnow() - timedelta(hours=24, minutes=1)
        self._create_inbound("201000000003", ts)
        result = can_send_session_message("201000000003")
        self.assertFalse(result["allowed"])

    def test_plus_prefix_normalized(self):
        from frappe_whatsapp.utils.window import can_send_session_message
        # Stored without +, queried with + prefix and spaces — must still match.
        ts = datetime.utcnow() - timedelta(hours=1)
        self._create_inbound("201000000004", ts)
        result = can_send_session_message("+20 1000000004")
        self.assertTrue(result["allowed"])
