# Upstream PR Drafts

Entries #1–#10 live in the axe_helpdesk_wa repo (transport-layer discoveries
made during the WinFi helpdesk integration). Entries below are generic
transport hardening from this fork.

---

## #11 — Per-sender inbound rate limit

**Title:** feat(webhook): per-sender inbound rate limit

**Rationale:** Protect the site from a single sender flooding the inbound
webhook — either a misbehaving client or a Meta retransmit storm. When
`inbound_rate_limit_per_minute > 0` on a WhatsApp Account, messages beyond
that per-sender-per-minute threshold are silently skipped (logged at WARNING)
and the webhook still returns 200 so Meta never re-queues them.

**Files:**
- `frappe_whatsapp/frappe_whatsapp/doctype/whatsapp_account/whatsapp_account.json` — new `inbound_rate_limit_per_minute` Int field (default 0 = disabled)
- `frappe_whatsapp/utils/webhook.py` — fixed-window Redis counter in the inbound message loop (after dedup, before persist)
- `frappe_whatsapp/utils/test_webhook.py` — `TestInboundRateLimit`: within-limit, over-limit, limit=0 cases

**Commit:** 2634b8e

---

## #12 — Configurable WhatsApp Notification Log retention

**Title:** feat(logs): configurable WhatsApp Notification Log retention

**Rationale:** WhatsApp Notification Logs accumulate one row per inbound
webhook call and are never pruned, causing unbounded table growth. Registering
the doctype in `default_log_clearing_doctypes` lets Frappe's built-in Log
Settings UI manage retention (default 30 days), consistent with how Error Log,
Email Queue, and other core log types are handled.

**Files:**
- `frappe_whatsapp/hooks.py` — `default_log_clearing_doctypes = {"WhatsApp Notification Log": 30}`
- `frappe_whatsapp/frappe_whatsapp/doctype/whatsapp_notification_log/whatsapp_notification_log.py` — `clear_old_logs(days=30)` staticmethod mirroring `error_log.py`
- `frappe_whatsapp/frappe_whatsapp/doctype/whatsapp_notification_log/test_whatsapp_notification_log.py` — `test_clear_old_logs_purges_old_keeps_recent`

**Commit:** (see Commit B hash)
