"""Runtime service entry points for Evarness.

Every server under this package reads settings, so the owner's saved setting changes
(ADR-0120) are applied here, when the package is first imported — before any server
module, or anything it imports, reads an ``EVARNESS_*`` value. Under a supervisor, each
server also watches for the app's restart request and exits for the supervisor to
bring it back with the new settings — unless ``EVARNESS_RESTART_ON_REQUEST=0`` (the VM's
governor, which owns the network namespace the other services share).
"""

from evarness.foundation.settings.env_overrides import apply_env_overrides
from evarness.foundation.settings.restart import exits_on_restart_request, watch_for_restart

apply_env_overrides()
if exits_on_restart_request():
    watch_for_restart()
