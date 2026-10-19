"""Research: the search-provider chain the ``research`` tool tries.

The engine itself is the ``research`` reference plugin
(``evarness/plugins_builtin/research/``); what lives in the core is the seam every
provider -- the plugin's own five and any other plugin's -- joins, so it outlives any
one plugin and the SDK can publish it (``evarness.sdk.research``).
"""
