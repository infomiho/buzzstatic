from __future__ import annotations

import os
import secrets
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


ADJECTIVES = ["cool", "fast", "blue", "red", "green", "happy", "swift", "bright", "calm", "bold"]
NOUNS = ["site", "page", "app", "web", "hub", "box", "lab", "dev", "net", "cloud"]

_LOCAL_CONTROL_HOSTS = {"localhost", "127.0.0.1", "::1", "testserver"}


def generate_subdomain() -> str:
    return f"{secrets.choice(ADJECTIVES)}-{secrets.choice(NOUNS)}-{secrets.randbelow(9000) + 1000}"


def _hostname(host: str | None) -> str:
    if not host:
        return ""

    try:
        parsed = urlsplit(f"//{host.strip()}")
        if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            return ""
        # urlsplit validates the port lazily; accessing it raises ValueError
        # on a malformed port, rejecting the host below.
        parsed.port
    except ValueError:
        return ""
    return (parsed.hostname or "").lower().rstrip(".")


def is_control_host(host: str | None, domain: str | None) -> bool:
    hostname = _hostname(host)
    domain = _hostname(domain)
    if domain:
        return hostname == domain
    return hostname in _LOCAL_CONTROL_HOSTS


def extract_subdomain(host: str | None, domain: str | None) -> str | None:
    hostname = _hostname(host)
    if not hostname:
        return None

    domain = _hostname(domain)
    if domain and hostname.endswith("." + domain):
        sub = hostname[: -(len(domain) + 1)]
        return sub if sub else None

    if not domain and hostname.endswith(".localhost"):
        return hostname.removesuffix(".localhost")
    return None


def write_file_atomically(path: Path, content: str, mode: int = 0o600) -> None:
    """Readers see the old file or the new one, never a partial write, even
    with concurrent writers. Each writer uses its own hidden `.tmp` sibling,
    which directory watchers that filter by extension ignore."""
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
