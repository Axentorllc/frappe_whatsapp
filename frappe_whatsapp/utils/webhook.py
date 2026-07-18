"""Webhook."""
import frappe
import hmac
import json
import requests
import time
from hashlib import sha256
from frappe import _
from werkzeug.wrappers import Response
import frappe.utils

from frappe_whatsapp.utils import get_whatsapp_account


@frappe.whitelist(allow_guest=True)
def webhook():
	"""Meta webhook."""
	if frappe.request.method == "GET":
		return get()
	return post()


def get():
	"""Get."""
	hub_challenge = frappe.form_dict.get("hub.challenge")
	verify_token = frappe.form_dict.get("hub.verify_token")
	webhook_verify_token = frappe.db.get_value(
		'WhatsApp Account',
		{"webhook_verify_token": verify_token},
		'webhook_verify_token'
	)
	if not webhook_verify_token:
		frappe.throw("No matching WhatsApp account")

	if frappe.form_dict.get("hub.verify_token") != webhook_verify_token:
		frappe.throw("Verify token does not match")

	return Response(hub_challenge, status=200)


def _verify_webhook_signature():
	"""Verify X-Hub-Signature-256 HMAC if an app_secret is configured.

	Returns True when no account has an app_secret (backwards-compatible skip)
	or when the signature is present and matches. Returns False when a secret
	is configured but the signature is absent or wrong.
	"""
	app_secret = _get_webhook_app_secret()
	if not app_secret:
		return True  # No secret configured — skip check (backwards compat)

	signature_header = frappe.request.headers.get("X-Hub-Signature-256", "")
	if not signature_header.startswith("sha256="):
		return False  # Secret configured but signature absent/malformed

	expected = "sha256=" + hmac.new(
		app_secret.encode() if isinstance(app_secret, str) else app_secret,
		frappe.request.get_data(),
		sha256,
	).hexdigest()
	return hmac.compare_digest(expected, signature_header)


def _get_webhook_app_secret():
	"""Return the app_secret of the first active WhatsApp Account that has one.

	Returns None when no active account has a secret (verification skipped —
	backwards compatible).
	"""
	from frappe.utils.password import get_decrypted_password

	accounts = frappe.get_all(
		"WhatsApp Account",
		filters={"status": "Active"},
		pluck="name",
	)
	for account_name in accounts:
		secret = get_decrypted_password(
			"WhatsApp Account", account_name, "app_secret", raise_exception=False
		)
		if secret:
			return secret
	return None


def _publish_inbound_event(doc):
	"""Publish a generic inbound event for subscribers (chat UIs, apps)."""
	frappe.publish_realtime(  # nosemgrep: frappe-realtime-pick-room -- site-wide fan-out; subscribers filter by 'from'
		"whatsapp_message",
		{"name": doc.name, "type": "Incoming", "from": doc.get("from")},
		after_commit=True,
	)


def _download_and_attach_media(doc, media_id, media_type, token, base_url, filename_hint=None):
	"""Download Meta media and attach to doc. Logs + notes on failure; never raises."""
	headers = {"Authorization": "Bearer " + token}
	logger = frappe.logger("frappe_whatsapp")
	try:
		response = requests.get(f"{base_url}{media_id}/", headers=headers, timeout=30)
		if response.status_code != 200:
			logger.error(
				f"media metadata fetch failed: status={response.status_code} media_id={media_id}"
			)
			doc.db_set("message", (doc.message or "") + f" [Media download failed: HTTP {response.status_code}]")
			return

		media_data = response.json()
		media_url = media_data.get("url")
		mime_type = media_data.get("mime_type", "application/octet-stream")
		file_extension = mime_type.split("/")[-1] if "/" in mime_type else "bin"

		# Preserve original filename when Meta provides it; fall back to hash
		if filename_hint:
			file_name = filename_hint
		else:
			file_name = f"{frappe.generate_hash(length=10)}.{file_extension}"

		media_response = requests.get(media_url, headers=headers, timeout=60)
		if media_response.status_code != 200:
			logger.error(
				f"media content fetch failed: status={media_response.status_code} url={media_url}"
			)
			doc.db_set("message", (doc.message or "") + f" [Media download failed: HTTP {media_response.status_code}]")
			return

		file = frappe.get_doc({
			"doctype": "File",
			"file_name": file_name,
			"attached_to_doctype": "WhatsApp Message",
			"attached_to_name": doc.name,
			"content": media_response.content,
			"attached_to_field": "attach",
		}).save(ignore_permissions=True)

		doc.db_set("attach", file.file_url)

	except Exception:
		logger.exception(f"media download error for media_id={media_id}")
		doc.db_set("message", (doc.message or "") + " [Media download error]")


def post():
	"""Post."""
	# HMAC verification must happen first, over the raw body, before any
	# processing or logging — a rejected request must not touch the DB.
	if not _verify_webhook_signature():
		return Response("Forbidden", status=403)

	data = frappe.local.form_dict
	frappe.get_doc({
		"doctype": "WhatsApp Notification Log",
		"template": "Webhook",
		"meta_data": json.dumps(data)
	}).insert(ignore_permissions=True)

	messages = []
	phone_id = None
	try:
		messages = data["entry"][0]["changes"][0]["value"].get("messages", [])
		phone_id = data.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {}).get("metadata", {}).get("phone_number_id")
	except KeyError:
		messages = data["entry"]["changes"][0]["value"].get("messages", [])
	sender_profile_name = next(
		(
			contact.get("profile", {}).get("name")
			for entry in data.get("entry", [])
			for change in entry.get("changes", [])
			for contact in change.get("value", {}).get("contacts", [])
		),
		None,
	)

	whatsapp_account = get_whatsapp_account(phone_id) if phone_id else None

	# Only `messages` events carry `metadata.phone_number_id`. Status-change
	# events (`message_template_status_update`, message status callbacks) have
	# no metadata, so `phone_id` is None and `whatsapp_account` is also None
	# for them by design. Gating the entire handler on `whatsapp_account`
	# silently drops every template-status update; gate only the message-
	# ingestion branch instead.
	if messages and not whatsapp_account:
		return

	if messages:
		for message in messages:
			message_type = message['type']
			mid = message.get('id')
			if mid and frappe.db.exists("WhatsApp Message", {"message_id": mid}):
				frappe.logger("frappe_whatsapp").debug(
					f"inbound: duplicate message_id={mid!r}; skipping"
				)
				continue
			is_reply = True if message.get('context') and 'forwarded' not in message.get('context') else False
			reply_to_message_id = message['context']['id'] if is_reply else None
			if message_type == 'text':
				doc = frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Incoming",
					"from": message['from'],
					"message": message['text']['body'],
					"message_id": message['id'],
					"reply_to_message_id": reply_to_message_id,
					"is_reply": is_reply,
					"content_type":message_type,
					"profile_name":sender_profile_name,
					"whatsapp_account":whatsapp_account.name
				})
				if message.get("referral"):
					doc.referral = json.dumps(message["referral"])
				doc.insert(ignore_permissions=True)
			elif message_type == 'reaction':
				frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Incoming",
					"from": message['from'],
					"message": message['reaction']['emoji'],
					"reply_to_message_id": message['reaction']['message_id'],
					"message_id": message['id'],
					"content_type": "reaction",
					"profile_name":sender_profile_name,
					"whatsapp_account":whatsapp_account.name
				}).insert(ignore_permissions=True)
			elif message_type == 'interactive':
				interactive_data = message['interactive']
				interactive_type = interactive_data.get('type')

				# Handle button reply
				if interactive_type == 'button_reply':
					frappe.get_doc({
						"doctype": "WhatsApp Message",
						"type": "Incoming",
						"from": message['from'],
						"message": interactive_data['button_reply']['id'],
						"message_id": message['id'],
						"reply_to_message_id": reply_to_message_id,
						"is_reply": is_reply,
						"content_type": "button",
						"profile_name": sender_profile_name,
						"whatsapp_account": whatsapp_account.name
					}).insert(ignore_permissions=True)
				# Handle list reply
				elif interactive_type == 'list_reply':
					frappe.get_doc({
						"doctype": "WhatsApp Message",
						"type": "Incoming",
						"from": message['from'],
						"message": interactive_data['list_reply']['id'],
						"message_id": message['id'],
						"reply_to_message_id": reply_to_message_id,
						"is_reply": is_reply,
						"content_type": "button",
						"profile_name": sender_profile_name,
						"whatsapp_account": whatsapp_account.name
					}).insert(ignore_permissions=True)
				# Handle WhatsApp Flows (nfm_reply)
				elif interactive_type == 'nfm_reply':
					nfm_reply = interactive_data['nfm_reply']
					response_json_str = nfm_reply.get('response_json', '{}')

					# Parse the response JSON
					try:
						flow_response = json.loads(response_json_str)
					except json.JSONDecodeError:
						flow_response = {}

					# Create a summary message from the flow response
					summary_parts = []
					for key, value in flow_response.items():
						if value:
							summary_parts.append(f"{key}: {value}")
					summary_message = ", ".join(summary_parts) if summary_parts else "Flow completed"

					msg_doc = frappe.get_doc({
						"doctype": "WhatsApp Message",
						"type": "Incoming",
						"from": message['from'],
						"message": summary_message,
						"message_id": message['id'],
						"reply_to_message_id": reply_to_message_id,
						"is_reply": is_reply,
						"content_type": "flow",
						"flow_response": json.dumps(flow_response),
						"profile_name": sender_profile_name,
						"whatsapp_account": whatsapp_account.name
					}).insert(ignore_permissions=True)

					# Publish realtime event for flow response
					frappe.publish_realtime(  # nosemgrep: frappe-realtime-pick-room -- intentional site-wide fan-out for chat UIs (whatsapp_chat companion app) listening for inbound flow responses
						"whatsapp_flow_response",
						{
							"phone": message['from'],
							"message_id": message['id'],
							"flow_response": flow_response,
							"whatsapp_account": whatsapp_account.name
						}
					)
			# NEW: Handle Shopping Cart / Orders from MPM
			elif message_type == 'order':
				order_data = message['order']

				# Inject the raw data into product_catalog_json
				frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Incoming",
					"from": message['from'],
					"message": _("New Order Received via WhatsApp"),
					"message_id": message['id'],
					"content_type": "order",
					"profile_name": sender_profile_name,
					"whatsapp_account": whatsapp_account.name,
					"product_catalog_json": json.dumps(order_data)
				}).insert(ignore_permissions=True)
			elif message_type in ["image", "audio", "video", "document"]:
				token = whatsapp_account.get_password("token")
				url = f"{whatsapp_account.url}/{whatsapp_account.version}/"

				media_id = message[message_type]["id"]
				headers = {
					'Authorization': 'Bearer ' + token

				}
				response = requests.get(f'{url}{media_id}/', headers=headers)

				if response.status_code == 200:
					media_data = response.json()
					media_url = media_data.get("url")
					mime_type = media_data.get("mime_type")
					file_extension = mime_type.split('/')[1]

					media_response = requests.get(media_url, headers=headers)
					if media_response.status_code == 200:

						file_data = media_response.content
						file_name = f"{frappe.generate_hash(length=10)}.{file_extension}"

						message_doc = frappe.get_doc({
							"doctype": "WhatsApp Message",
							"type": "Incoming",
							"from": message['from'],
							"message_id": message['id'],
							"reply_to_message_id": reply_to_message_id,
							"is_reply": is_reply,
							"message": message[message_type].get("caption", ""),
							"content_type" : message_type,
							"profile_name":sender_profile_name,
							"whatsapp_account":whatsapp_account.name
						}).insert(ignore_permissions=True)

						file = frappe.get_doc(
							{
								"doctype": "File",
								"file_name": file_name,
								"attached_to_doctype": "WhatsApp Message",
								"attached_to_name": message_doc.name,
								"content": file_data,
								"attached_to_field": "attach"
							}
						).save(ignore_permissions=True)


						message_doc.attach = file.file_url
						message_doc.save()
			elif message_type == "button":
				frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Incoming",
					"from": message['from'],
					"message": message['button']['text'],
					"message_id": message['id'],
					"reply_to_message_id": reply_to_message_id,
					"is_reply": is_reply,
					"content_type": message_type,
					"profile_name":sender_profile_name,
					"whatsapp_account":whatsapp_account.name
				}).insert(ignore_permissions=True)
			elif message_type == "location":
				loc = message.get("location", {})
				parts = [f"{loc.get('latitude')},{loc.get('longitude')}"]
				if loc.get("name"):
					parts.append(loc["name"])
				if loc.get("address"):
					parts.append(loc["address"])
				doc = frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Incoming",
					"from": message['from'],
					"message": " | ".join(parts),
					"message_id": message['id'],
					"content_type": "location",
					"profile_name": sender_profile_name,
					"whatsapp_account": whatsapp_account.name,
				})
				if message.get("referral"):
					doc.referral = json.dumps(message["referral"])
				doc.insert(ignore_permissions=True)
				_publish_inbound_event(doc)
			elif message_type == "contacts":
				parts = []
				for c in message.get("contacts", []):
					name_obj = c.get("name", {})
					display = name_obj.get("formatted_name") or name_obj.get("first_name", "")
					phones = [p.get("phone", "") for p in c.get("phones", [])]
					parts.append(f"{display}: {', '.join(phones)}" if phones else display)
				doc = frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Incoming",
					"from": message['from'],
					"message": "; ".join(parts) if parts else "[Contacts]",
					"message_id": message['id'],
					"content_type": "contacts",
					"profile_name": sender_profile_name,
					"whatsapp_account": whatsapp_account.name,
				})
				if message.get("referral"):
					doc.referral = json.dumps(message["referral"])
				doc.insert(ignore_permissions=True)
				_publish_inbound_event(doc)
			elif message_type == "sticker":
				# Sticker is a media type — download like image
				token = whatsapp_account.get_password("token")
				url = f"{whatsapp_account.url}/{whatsapp_account.version}/"
				media_id = message.get("sticker", {}).get("id")
				doc = frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Incoming",
					"from": message['from'],
					"message": "",
					"message_id": message['id'],
					"content_type": "sticker",
					"profile_name": sender_profile_name,
					"whatsapp_account": whatsapp_account.name,
				})
				if message.get("referral"):
					doc.referral = json.dumps(message["referral"])
				doc.insert(ignore_permissions=True)
				if media_id:
					_download_and_attach_media(doc, media_id, "sticker", token, url)
				_publish_inbound_event(doc)
			else:
				# Unsupported / unknown message type — persist placeholder, never drop
				doc = frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Incoming",
					"from": message['from'],
					"message_id": message['id'],
					"message": f"[Unsupported message type: {message_type}]",
					"content_type": "unsupported",
					"profile_name": sender_profile_name,
					"whatsapp_account": whatsapp_account.name,
				})
				if message.get("referral"):
					doc.referral = json.dumps(message["referral"])
				doc.insert(ignore_permissions=True)
				_publish_inbound_event(doc)

	else:
		changes = None
		try:
			changes = data["entry"][0]["changes"][0]
		except KeyError:
			changes = data["entry"]["changes"][0]
		update_status(changes)
	return

def update_status(data):
	"""Update status hook."""
	if data.get("field") == "message_template_status_update":
		update_template_status(data['value'])

	elif data.get("field") == "messages":
		update_message_status(data['value'])

def update_template_status(data):
	"""Update template status."""
	frappe.db.sql(
		"""UPDATE `tabWhatsApp Templates`
		SET status = %(event)s
		WHERE id = %(message_template_id)s""",
		data
	)

def update_message_status(data):
	"""Update message status."""
	id = data['statuses'][0]['id']
	status = data['statuses'][0]['status']
	conversation = data['statuses'][0].get('conversation', {}).get('id')
	name = frappe.db.get_value("WhatsApp Message", filters={"message_id": id})

	if not name:
		frappe.logger("frappe_whatsapp").debug(
			f"update_message_status: no WhatsApp Message for message_id={id!r}; skipping"
		)
		return

	doc = frappe.get_doc("WhatsApp Message", name)
	doc.status = status
	if conversation:
		doc.conversation_id = conversation
	doc.save(ignore_permissions=True)
