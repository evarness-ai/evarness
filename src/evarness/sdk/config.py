"""Where Evarness reads its config, for plugins.

A plugin that ships defaults in the repo's ``config/`` tree (a category list, a policy
YAML) finds them here rather than with a bare ``Path("config/...")``, which is relative
to whatever directory the process happens to run in. ``config_dir()`` resolves, in
order: ``EVARNESS_CONFIG_DIR``, the checkout's ``config/``, the defaults packaged into the
installed wheel (see ``evarness.foundation.paths``).

``evarness_home()`` is where this Evarness lives (``$EVARNESS_HOME``, else ``~/.evarness``) and
``workspace_dir()`` the owner's workspace inside it: what a plugin keeps beside the
identity files (an account's category proposals) goes there, never under a bare
``Path.home() / ".evarness"``, which ignores a relocated home.

``public_base_url()`` is the address the owner reaches this Evarness at from outside
(``EVARNESS_PUBLIC_URL``, named by ``PUBLIC_URL_ENV``), or ``None``: what an OAuth
redirect or a link in a notification must point at.
"""

from __future__ import annotations

from evarness.foundation.paths import config_dir, config_path, evarness_home, workspace_dir
from evarness.foundation.public_url import PUBLIC_URL_ENV, public_base_url

__all__ = [
    "PUBLIC_URL_ENV",
    "config_dir",
    "config_path",
    "evarness_home",
    "public_base_url",
    "workspace_dir",
]
