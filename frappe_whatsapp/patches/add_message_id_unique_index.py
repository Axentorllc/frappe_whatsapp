import frappe


def execute():
	"""Add UNIQUE index on tabWhatsApp Message.message_id.

	Normalizes empty-string message_id to NULL first (MariaDB UNIQUE allows
	multiple NULLs, but treats '' as a single unique value which would block
	a second insert of any draft without a message_id).
	"""
	frappe.db.sql(
		"""
		UPDATE `tabWhatsApp Message`
		SET message_id = NULL
		WHERE message_id = ''
		"""
	)
	frappe.db.commit()  # DDL below implicitly commits; flush the UPDATE first
	# Only add if not already present (idempotent)
	existing = frappe.db.sql(
		"""
		SELECT INDEX_NAME FROM INFORMATION_SCHEMA.STATISTICS
		WHERE TABLE_SCHEMA = DATABASE()
		  AND TABLE_NAME = 'tabWhatsApp Message'
		  AND INDEX_NAME = 'unique_message_id'
		"""
	)
	if not existing:
		frappe.db.sql(
			"""
			ALTER TABLE `tabWhatsApp Message`
			ADD UNIQUE INDEX `unique_message_id` (`message_id`)
			"""
		)
