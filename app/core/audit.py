"""Section AC - immutable, non-blocking audit logging of state mutations."""
from __future__ import annotations

import hashlib
import json
import logging
import time

_audit = logging.getLogger("investwise.audit")


def audit(*, method: str, path: str, ip: str, role: str, payload: bytes) -> None:
    entry = {
        "ts": time.time(),
        "method": method,
        "route": path,
        "origin_ip": ip,
        "role": role,
        "payload_sha256": hashlib.sha256(payload or b"").hexdigest(),
    }
    _audit.info(json.dumps(entry))  # append-only to the log stream
