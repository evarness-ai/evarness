"""The Gmail plugin — the mailbox provider (OSS plan M5.7, track A slice 2).

``gmail_fetch``, ``gmail_attachments`` and ``gmail_oauth`` moved here unchanged from
``evarness_personal.email`` / ``evarness.memory.identity``; :mod:`provider` wraps them as the
``MailProvider`` the core's ``email.providers`` registry dispatches to.
"""
