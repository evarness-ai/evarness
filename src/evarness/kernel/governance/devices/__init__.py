"""Paired devices package (ADR-0117)."""

from evarness.kernel.governance.devices.service import (
    PAIRING_CODE_TTL,
    PAIRING_MAX_ATTEMPTS,
    TOKEN_PREFIX,
    DeviceService,
    PairedDevice,
    PairingCode,
    PairingRefusedError,
    PairingThrottledError,
)
from evarness.kernel.governance.devices.store import (
    DeviceNotFoundError,
    DeviceRow,
    DeviceStore,
)
from evarness.kernel.governance.devices.throttle import (
    CLAIM_FAILURE_LIMIT,
    CLAIM_FAILURE_WINDOW,
    ClaimThrottle,
)

__all__ = [
    "CLAIM_FAILURE_LIMIT",
    "CLAIM_FAILURE_WINDOW",
    "PAIRING_CODE_TTL",
    "PAIRING_MAX_ATTEMPTS",
    "TOKEN_PREFIX",
    "ClaimThrottle",
    "DeviceNotFoundError",
    "DeviceRow",
    "DeviceService",
    "DeviceStore",
    "PairedDevice",
    "PairingCode",
    "PairingRefusedError",
    "PairingThrottledError",
]
