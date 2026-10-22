"""``docs_url``: the published address of a page under ``docs/``."""

from __future__ import annotations

from evarness.foundation.docs_site import DOCS_SITE, docs_url


def test_a_page_becomes_its_site_address() -> None:
    assert docs_url("getting-started/install") == DOCS_SITE + "getting-started/install/"
    assert docs_url("getting-started/install.md") == DOCS_SITE + "getting-started/install/"
    assert docs_url("/guides/setup-gotchas/") == DOCS_SITE + "guides/setup-gotchas/"


def test_an_anchor_and_an_index_page_are_kept() -> None:
    assert docs_url("getting-started/install#put-evarness-on-your-path") == (
        DOCS_SITE + "getting-started/install/#put-evarness-on-your-path"
    )
    assert docs_url("adr/index") == DOCS_SITE + "adr/"
    assert docs_url("") == DOCS_SITE


def test_the_plugin_side_copy_is_the_same_site() -> None:
    from evarness_personal.email import docs_site as plugin_side

    assert plugin_side.DOCS_SITE == DOCS_SITE
    assert plugin_side.GMAIL_OAUTH_CLIENT_GUIDE == docs_url("getting-started/gmail-oauth-client")
    assert plugin_side.CONNECT_MAILBOX_GUIDE == docs_url("getting-started/connect-your-mailbox")
