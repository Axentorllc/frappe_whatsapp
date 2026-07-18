import frappe


def execute():
	"""Add UNIQUE index on tabWhatsApp Message.message_id.

	1. Normalizes empty-string message_id to NULL (MariaDB UNIQUE allows
	   multiple NULLs but treats '' as one unique value).
	2. Collapses genuine duplicate non-empty message_ids caused by Meta
	   retransmits: keeps the oldest row, NULLs the message_id on all
	   newer duplicates (rows are preserved — they may be linked).
	3. Adds the UNIQUE index idempotently.
	"""
	logger = frappe.logger("frappe_whatsapp")

	# Step 1 — empty string → NULL
	frappe.db.sql(
		"""
		UPDATE `tabWhatsApp Message`
		SET message_id = NULL
		WHERE message_id = ''
		"""
	)
	frappe.db.commit()  # flush DML before DDL (MariaDB DDL triggers implicit commit)

	# Step 2 — collapse duplicates: NULL out message_id on all but the oldest row
	dupes = frappe.db.sql(
		"""
		SELECT message_id, COUNT(*) AS c
		FROM `tabWhatsApp Message`
		WHERE message_id IS NOT NULL AND message_id != ''
		GROUP BY message_id
		HAVING c > 1
		""",
		as_dict=True,
	)
	collapsed = 0
	for row in dupes:
		mid = row["message_id"]
		# Keep the first (oldest) name; NULL out the rest
		names = frappe.db.sql(
			"""
			SELECT name FROM `tabWhatsApp Message`
			WHERE message_id = %s
			ORDER BY creation ASC
			""",
			(mid,),
			pluck="name",
		)
		for name in names[1:]:
			frappe.db.sql(
				"UPDATE `tabWhatsApp Message` SET message_id = NULL WHERE name = %s",
				(name,),
			)
			collapsed += 1
	if collapsed:
		logger.warning(
			f"add_message_id_unique_index: collapsed {collapsed} duplicate message_id row(s) "
			"(message_id set to NULL on newer duplicates; rows preserved)"
		)
	frappe.db.commit()  # flush step-2 DML before the ALTER

	# Step 3 — add index idempotently
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
