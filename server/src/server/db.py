from __future__ import annotations

import os
import re
import sqlite3
import threading
from collections.abc import Callable, Generator
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .access.schema import _buzz_access, _buzz_access_readers, _buzz_access_site_level
from .analytics import init_analytics_schema
from .custom_domains.schema import (
    _automatic_domain_transitions,
    _automatic_transition_retarget,
    _cloudflare_activation,
    _cloudflare_diagnostics,
    _custom_domain_activation,
    _custom_domain_claims,
    _custom_domain_routing,
    _domain_path_evidence,
    _multiple_custom_domains,
    _transition_target_ttl,
)

Migration = Callable[[sqlite3.Connection], None]

BACKUPS_KEPT = 2
BACKUP_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"
BACKUP_NAME = re.compile(r"data-schema(?P<schema>\d+)-(?P<taken_at>\d{8}T\d{6}Z)\.db")


def _configure_connection(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")


def _base_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS sites (
        name TEXT PRIMARY KEY,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        size_bytes INTEGER,
        owner_id INTEGER)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        github_id INTEGER UNIQUE NOT NULL,
        github_login TEXT NOT NULL,
        github_name TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        expires_at DATETIME NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS deployment_tokens (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        site_name TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        expires_at DATETIME,
        last_used_at DATETIME,
        FOREIGN KEY (site_name) REFERENCES sites(name) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)""")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(sites)")}
    if "owner_id" not in columns:
        conn.execute("ALTER TABLE sites ADD COLUMN owner_id INTEGER")
    init_analytics_schema(conn)


def _webauthn_credentials(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE users ADD COLUMN webauthn_user_handle BLOB")
    conn.execute(
        """CREATE UNIQUE INDEX idx_users_webauthn_user_handle
        ON users(webauthn_user_handle) WHERE webauthn_user_handle IS NOT NULL"""
    )
    conn.execute("""CREATE TABLE webauthn_credentials (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        public_key BLOB NOT NULL,
        sign_count INTEGER NOT NULL,
        transports TEXT,
        backed_up INTEGER NOT NULL DEFAULT 0,
        name TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        last_used_at DATETIME,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)""")
    conn.execute(
        "CREATE INDEX idx_webauthn_credentials_user_id ON webauthn_credentials(user_id)"
    )


def _principal_identities(conn: sqlite3.Connection) -> None:
    """Separate authentication identity from control-plane admission.

    The existing users table remains the stable Principal record so installed
    databases keep all ownership, session, token, and Access foreign keys.
    """
    conn.execute(
        "ALTER TABLE users ADD COLUMN control_admitted INTEGER NOT NULL DEFAULT 1"
    )
    conn.execute("""CREATE TABLE principal_identities (
        user_id INTEGER NOT NULL,
        provider TEXT NOT NULL,
        subject TEXT NOT NULL,
        login_snapshot TEXT NOT NULL,
        name_snapshot TEXT,
        avatar_url_snapshot TEXT,
        last_authenticated_at DATETIME,
        PRIMARY KEY (provider, subject),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)""")
    conn.execute(
        "CREATE INDEX idx_principal_identities_user ON principal_identities(user_id)"
    )
    user_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()
    }
    if {"github_id", "github_login", "github_name"} <= user_columns:
        conn.execute(
            "INSERT INTO principal_identities "
            "(user_id, provider, subject, login_snapshot, name_snapshot) "
            "SELECT id, 'github', CAST(github_id AS TEXT), github_login, github_name FROM users"
        )


def _site_deployments(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE site_deployments (
        site_name TEXT NOT NULL,
        deployment_number INTEGER NOT NULL,
        deployed_at DATETIME NOT NULL,
        size_bytes INTEGER NOT NULL,
        source TEXT NOT NULL CHECK(source IN ('dashboard', 'api')),
        actor TEXT NOT NULL,
        credential TEXT,
        PRIMARY KEY (site_name, deployment_number),
        FOREIGN KEY (site_name) REFERENCES sites(name) ON DELETE CASCADE)""")
    conn.execute("""CREATE TABLE active_site_deployments (
        site_name TEXT PRIMARY KEY,
        deployment_number INTEGER NOT NULL,
        FOREIGN KEY (site_name) REFERENCES sites(name) ON DELETE CASCADE,
        FOREIGN KEY (site_name, deployment_number)
            REFERENCES site_deployments(site_name, deployment_number)
            ON DELETE CASCADE)""")


MIGRATIONS: tuple[Migration, ...] = (
    _base_schema,
    _custom_domain_claims,
    _custom_domain_routing,
    _custom_domain_activation,
    _multiple_custom_domains,
    _cloudflare_diagnostics,
    _cloudflare_activation,
    _automatic_domain_transitions,
    _transition_target_ttl,
    _domain_path_evidence,
    _automatic_transition_retarget,
    _webauthn_credentials,
    _buzz_access,
    _buzz_access_site_level,
    _principal_identities,
    _buzz_access_readers,
    _site_deployments,
)


class ReadConnection:
    """A long-lived connection for hot-path reads, shared across threads.

    Opened on first borrow and reused for the process lifetime, so callers
    skip the per-connect schema parse that dominates a fresh connection.
    borrow() serializes access; hold it only for the duration of the queries.
    """

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    @contextmanager
    def borrow(self) -> Generator[sqlite3.Connection, None, None]:
        with self._lock:
            if self._conn is None:
                conn = sqlite3.connect(self._path, check_same_thread=False)
                _configure_connection(conn)
                conn.row_factory = sqlite3.Row
                self._conn = conn
            yield self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


@dataclass(frozen=True)
class SchemaBackup:
    path: Path
    schema_version: int
    taken_at: str


class SchemaBackups:
    """Pre-migration snapshots, so an upgrade can be rolled back by restoring
    the database the previous server version last wrote."""

    def __init__(self, directory: Path):
        self.directory = directory

    def take(self, source_path: Path, schema_version: int) -> Path:
        """Copy the committed database through a separate connection. The
        caller may hold the write lock, which a backup from its own connection
        would wait on forever."""
        taken_at = datetime.now(timezone.utc).strftime(BACKUP_TIMESTAMP_FORMAT)
        target = self.directory / f"data-schema{schema_version}-{taken_at}.db"
        partial = target.with_suffix(".partial")
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            # The backup holds the same sessions and tokens as the database.
            os.close(os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600))
            with (
                closing(sqlite3.connect(source_path)) as source,
                closing(sqlite3.connect(partial)) as destination,
            ):
                source.backup(destination)
            os.replace(partial, target)
        except (OSError, sqlite3.Error) as exc:
            with suppress(OSError):
                partial.unlink(missing_ok=True)
            raise RuntimeError(
                f"Could not back up the database to {self.directory} before migrating: {exc}"
            ) from exc
        self._prune()
        return target

    def existing(self) -> list[SchemaBackup]:
        if not self.directory.is_dir():
            return []
        backups = []
        for path in self.directory.iterdir():
            match = BACKUP_NAME.fullmatch(path.name)
            if match:
                backups.append(SchemaBackup(path, int(match["schema"]), match["taken_at"]))
        return sorted(backups, key=lambda backup: backup.taken_at, reverse=True)

    def newest_restorable(self, supported_version: int) -> SchemaBackup | None:
        return next(
            (backup for backup in self.existing() if backup.schema_version <= supported_version),
            None,
        )

    def _prune(self) -> None:
        for backup in self.existing()[BACKUPS_KEPT:]:
            backup.path.unlink(missing_ok=True)


def _has_schema(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone() is not None


class Database:
    def __init__(self, path: Path, backups_dir: Path | None = None):
        self._path = path
        self.backups = SchemaBackups(backups_dir or path.parent / "backups")

    def reader(self) -> ReadConnection:
        return ReadConnection(self._path)

    def init(self) -> None:
        conn = sqlite3.connect(self._path)
        try:
            _configure_connection(conn)
            conn.execute("BEGIN IMMEDIATE")
            current_version = conn.execute("PRAGMA user_version").fetchone()[0]
            if current_version > len(MIGRATIONS):
                raise RuntimeError(self._newer_schema_message(current_version))
            if current_version < len(MIGRATIONS) and _has_schema(conn):
                backup = self.backups.take(self._path, current_version)
                print(f"Backed up database schema version {current_version} to {backup}")
            for version, migration in enumerate(MIGRATIONS, start=1):
                if version <= current_version:
                    continue
                migration(conn)
                conn.execute(f"PRAGMA user_version = {version}")
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(
                    "Database contains foreign-key violations; restore or repair it before starting Buzz"
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _newer_schema_message(self, current_version: int) -> str:
        supported = len(MIGRATIONS)
        message = (
            f"Database schema version {current_version} is newer than supported "
            f"version {supported}: a newer Buzz server already migrated {self._path}. "
            "Run that newer version again"
        )
        backup = self.backups.newest_restorable(supported)
        if backup is None:
            return f"{message}. No backup restorable by this version exists in {self.backups.directory}."
        return (
            f"{message}, or stop Buzz and restore {backup.path} over {self._path} "
            f"(taken {backup.taken_at}, schema version {backup.schema_version}). "
            "Restoring discards changes made since that backup."
        )

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self._path)
        _configure_connection(conn)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
