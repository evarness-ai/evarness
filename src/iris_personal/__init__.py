"""``iris_personal`` is the 0.1.x name of :mod:`evarness_personal`; this shim keeps the
imports working for one deprecation cycle. Removed in 0.3.0."""

from evarness.foundation.legacy import install_import_alias

install_import_alias("iris_personal", "evarness_personal")
