"""Connection diagnostics: privacy-safe log ingest + digests (no Telegram push)."""
from __future__ import annotations

DIGEST_ROOT = "/var/log/xeno/digests"
NL_ACCESS_LOG = "/var/log/xeno/nl-relay-access.log"
NL_ERROR_LOG = "/var/log/xeno/nl-relay-error.log"
RU_ACCESS_LOG = "/var/log/xeno/ru-access.log"
RU_ERROR_LOG = "/var/log/xeno/ru-error.log"
HOP_EMAIL = "xeno-relay-hop"
