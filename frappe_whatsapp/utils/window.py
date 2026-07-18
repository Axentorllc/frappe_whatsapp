"""24h messaging window primitive.

Returns whether a session (free-form) message can be sent to a number, based on
the last inbound WhatsApp Message from that number. Callers enforce policy;
this module only computes the window state.

ponytail: single-DB read, correct for typical volume. Upgrade to a Redis cache
          if the query becomes a bottleneck under high concurrent send traffic.
"""
import frappe
from datetime import datetime, timedelta


_WINDOW_HOURS = 24


@frappe.whitelist()
def can_send_session_message(to_number):
	"""Return window state for sending a session (non-template) message.

	Restricted to System Manager to prevent IDOR — any authenticated user
	probing any number's last-inbound timestamp would be an information leak.
	Server-side callers (the glue app, running as Administrator) are unaffected.

	Args:
		to_number: recipient number in any format (+ prefix, spaces stripped).

	Returns:
		dict with keys:
			allowed (bool): True if within the 24h window.
			window_expires_at (str|None): ISO datetime when window closes, or None.
			last_inbound_at (str|None): ISO datetime of last inbound, or None.
	"""
	frappe.only_for("System Manager")
	normalized = _normalize_number(to_number)

	result = frappe.db.get_value(
		"WhatsApp Message",
		filters={"type": "Incoming", "from": normalized},
		fieldname="creation",
		order_by="creation desc",
	)

	if not result and normalized != to_number:
		# Fall back to the raw number in case it was stored differently
		result = frappe.db.get_value(
			"WhatsApp Message",
			filters={"type": "Incoming", "from": to_number},
			fieldname="creation",
			order_by="creation desc",
		)

	if not result:
		return {"allowed": False, "window_expires_at": None, "last_inbound_at": None}

	last_inbound_at = result  # frappe returns a datetime object
	if isinstance(last_inbound_at, str):
		last_inbound_at = datetime.fromisoformat(last_inbound_at)

	expires_at = last_inbound_at + timedelta(hours=_WINDOW_HOURS)
	allowed = datetime.utcnow() < expires_at

	return {
		"allowed": allowed,
		"window_expires_at": expires_at.isoformat() if allowed else None,
		"last_inbound_at": last_inbound_at.isoformat(),
	}


def _normalize_number(number):
	"""Strip leading + and spaces. Country-agnostic."""
	return number.lstrip("+").replace(" ", "").strip()
