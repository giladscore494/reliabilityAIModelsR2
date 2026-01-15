import hashlib
import os
from ipaddress import ip_address, ip_network

TERMS_VERSION = os.environ.get("TERMS_VERSION", "2026-01-14")
PRIVACY_VERSION = os.environ.get("PRIVACY_VERSION", "2026-01-14")
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "support@yedaarechev.com")
LEGAL_IP_HASH_SALT = os.environ.get("LEGAL_IP_HASH_SALT", "").strip()


def normalize_legal_ip(raw_ip: str) -> str:
    """
    Reduce IP precision before storing consent audit records.
    This keeps auditability while limiting exposure of full IP data.
    """
    if not raw_ip:
        return "unknown"
    if LEGAL_IP_HASH_SALT:
        digest = hashlib.sha256(f"{LEGAL_IP_HASH_SALT}{raw_ip}".encode("utf-8")).hexdigest()
        return digest
    try:
        parsed = ip_address(raw_ip)
    except ValueError:
        return raw_ip[:64]
    if parsed.version == 4:
        parts = raw_ip.split(".")
        if len(parts) == 4:
            parts[-1] = "0"
            return ".".join(parts)
        return raw_ip
    network = ip_network(f"{parsed}/64", strict=False)
    return str(network.network_address)


def parse_legal_confirm(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "on")
