"""Normalize xray error / access outcomes into stable classes (no destinations)."""
from __future__ import annotations

import re

REALITY_HANDSHAKE = "reality_handshake"
SNI_MISMATCH = "sni_mismatch"
REJECTED = "rejected"
XHTTP_EOF = "xhttp_eof"
XHTTP_VERSION = "xhttp_version"
OTHER = "other"

_HANDSHAKE = re.compile(
    r"handshake did not complete|failed to read client hello|REALITY|tls:|x509:",
    re.I,
)
_SNI = re.compile(r"server name mismatch|invalid server name|unrecognized name", re.I)
_REJECT = re.compile(r"rejected|connection refused|auth failed|not found", re.I)
_XHTTP_EOF = re.compile(r"firstLen\s*=\s*0|failed to read request version.*EOF|\bEOF\b.*vless", re.I)
_XHTTP_VER = re.compile(r"unexpected response version|Expecting 0 but actually", re.I)


def classify_error_line(line: str) -> str:
    if _SNI.search(line):
        return SNI_MISMATCH
    if _XHTTP_VER.search(line):
        return XHTTP_VERSION
    if _XHTTP_EOF.search(line):
        return XHTTP_EOF
    if _HANDSHAKE.search(line):
        return REALITY_HANDSHAKE
    if _REJECT.search(line):
        return REJECTED
    return OTHER


def classify_access_action(action: str) -> str | None:
    a = (action or "").lower()
    if a == "accepted":
        return None
    if a in ("rejected", "blocked"):
        return REJECTED
    return OTHER
