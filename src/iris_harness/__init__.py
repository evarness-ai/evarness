"""``iris_harness`` is the 0.1.x name of :mod:`evarness`; this shim keeps the imports
working for one deprecation cycle. Removed in 0.3.0. Change ``iris_harness`` to
``evarness`` in your imports."""

from evarness.foundation.legacy import install_import_alias

install_import_alias("iris_harness", "evarness")
