"""The published documentation site, for the email plugins' messages.

A copy of ``evarness.foundation.docs_site``: this tree imports only the SDK, and a docs
address is not an SDK promise. Keep the two in step.
"""

from __future__ import annotations

DOCS_SITE = "https://evarness-ai.github.io/evarness/"

#: The one-time Google Cloud OAuth client the Gmail login needs.
GMAIL_OAUTH_CLIENT_GUIDE = DOCS_SITE + "getting-started/gmail-oauth-client/"
#: Every provider's IMAP server, port and where its app password comes from.
CONNECT_MAILBOX_GUIDE = DOCS_SITE + "getting-started/connect-your-mailbox/"

__all__ = ["CONNECT_MAILBOX_GUIDE", "DOCS_SITE", "GMAIL_OAUTH_CLIENT_GUIDE"]
