"""Writes Buzz-owned documents into a Traefik file-provider directory.

The directory is shared with the proxy owner (Coolify writes its own files
there), and Traefik merges every `.yaml`, `.yml` and `.toml` file in it,
hidden ones included. A file that fails to parse freezes that whole merged
configuration while Traefik runs and drops all of it on the next restart, so
every write is validated first and published with an atomic rename. Documents
are written as JSON, which is valid YAML.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .utils import write_file_atomically

NAME_PATTERN = re.compile(r"buzz(-[a-z0-9]+)*")
TOP_LEVEL_SECTIONS = frozenset({"http", "tcp", "udp", "tls"})


class DynamicConfigError(ValueError):
    pass


class TraefikDynamicFile:
    """One file per owner, named `buzz-<purpose>.yaml`."""

    def __init__(self, directory: Path, name: str):
        if not NAME_PATTERN.fullmatch(name):
            raise ValueError(f"Dynamic config names look like 'buzz-<purpose>', got {name!r}")
        self.path = directory / f"{name}.yaml"

    def write(self, document: Mapping[str, Any]) -> bool:
        """Publishes the document and reports whether the file changed."""
        content = serialize(document)
        try:
            if self.path.read_text() == content:
                return False
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            raise DynamicConfigError(f"{self.path} is unreadable: {exc}") from exc
        try:
            write_file_atomically(self.path, content)
        except OSError as exc:
            raise DynamicConfigError(f"Cannot write {self.path}: {exc}") from exc
        return True


def empty_sections(document: Mapping[str, Any]) -> list[str]:
    empty = []
    for name, section in document.items():
        if not isinstance(section, Mapping):
            continue
        if not section:
            empty.append(name)
        empty.extend(
            f"{name}.{child}"
            for child, value in section.items()
            if isinstance(value, Mapping) and not value
        )
    return empty


def serialize(document: Mapping[str, Any]) -> str:
    """Traefik runs file content through Go templates, so template delimiters
    must not appear, and it rejects empty sections such as `routers: {}`."""
    if not isinstance(document, Mapping) or not document:
        raise DynamicConfigError("Dynamic config must be a non-empty object")
    if unknown := set(document) - TOP_LEVEL_SECTIONS:
        raise DynamicConfigError(f"Unknown dynamic config sections: {sorted(unknown, key=str)}")
    if empty := empty_sections(document):
        raise DynamicConfigError(f"Dynamic config has empty sections: {empty}")
    try:
        content = json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n"
    except (TypeError, ValueError) as exc:
        raise DynamicConfigError(f"Dynamic config is not serializable: {exc}") from exc
    if "{{" in content or "}}" in content:
        raise DynamicConfigError("Dynamic config contains template delimiters")
    return content
