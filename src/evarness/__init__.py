"""Evarness package root.

Importing the package is the earliest moment the process runs Evarness code, so this is
where the names the 0.1.x releases used are mapped onto the current ones: an ``IRIS_*``
environment variable is copied to its ``EVARNESS_*`` name when that name is unset, with
one warning naming the variables. The copies are removed in 0.3.0.
"""

from evarness.foundation.legacy import alias_legacy_environment

__all__ = ["__version__"]

__version__ = "0.2.5"

alias_legacy_environment()
