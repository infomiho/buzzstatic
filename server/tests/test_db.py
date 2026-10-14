import sqlite3
import stat

import pytest

from server.db import Database, MIGRATIONS, _configure_connection


def test_fresh_database_runs_all_migrations(tmp_path):
    path = tmp_path / "data.db"

    Database(path).init()

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "sites" in tables
        assert "custom_domain_claims" in tables
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(custom_domain_claims)")
        }
        assert {
            "activated_at",
            "activation_checked_at",
            "activation_error",
            "claim_mode",
        } <= columns
        indexes = {
            row[1]: row[2]
            for row in conn.execute("PRAGMA index_list(custom_domain_claims)")
        }
        assert "custom_domain_claims_active_site" not in indexes
        assert indexes["custom_domain_claims_site_status"] == 0


def test_existing_unversioned_database_upgrades_without_data_loss(tmp_path):
    path = tmp_path / "data.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE sites (name TEXT PRIMARY KEY, created_at TEXT, size_bytes INTEGER)")
        conn.execute("INSERT INTO sites (name) VALUES ('existing-site')")

    Database(path).init()

    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM sites").fetchone()[0] == "existing-site"
        columns = {row[1] for row in conn.execute("PRAGMA table_info(sites)")}
        assert "owner_id" in columns
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)


def test_version_four_database_upgrades_to_multiple_aliases(tmp_path):
    path = tmp_path / "data.db"
    with sqlite3.connect(path) as conn:
        for migration in MIGRATIONS[:4]:
            migration(conn)
        conn.execute("PRAGMA user_version = 4")
        conn.execute("INSERT INTO sites (name) VALUES ('existing-site')")
        conn.execute(
            """INSERT INTO custom_domain_claims
            (hostname, site_name, verification_token, status, created_at, expires_at)
            VALUES ('one.example.com', 'existing-site', 'token-one', 'pending',
                    '2026-07-16T00:00:00+00:00', '2026-07-17T00:00:00+00:00')"""
        )

    Database(path).init()

    with sqlite3.connect(path) as conn:
        conn.execute(
            """INSERT INTO custom_domain_claims
            (hostname, site_name, verification_token, status, created_at, expires_at)
            VALUES ('two.example.com', 'existing-site', 'token-two', 'pending',
                    '2026-07-16T00:00:00+00:00', '2026-07-17T00:00:00+00:00')"""
        )
        assert conn.execute(
            "SELECT COUNT(*) FROM custom_domain_claims WHERE site_name = 'existing-site'"
        ).fetchone()[0] == 2
        assert {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT claim_mode FROM custom_domain_claims"
            )
        } == {"direct"}


def test_cloudflare_diagnostic_data_survives_activation_migration(tmp_path):
    path = tmp_path / "data.db"
    with sqlite3.connect(path) as conn:
        for migration in MIGRATIONS[:6]:
            migration(conn)
        conn.execute("PRAGMA user_version = 6")
        conn.execute("INSERT INTO sites (name) VALUES ('existing-site')")
        claim_id = conn.execute(
            """INSERT INTO custom_domain_claims
            (hostname, site_name, verification_token, status, created_at, expires_at,
             route_status, route_generation, claim_mode)
            VALUES ('one.example.com', 'existing-site', 'token-one', 'verified',
                    '2026-07-16T00:00:00+00:00', '2099-07-17T00:00:00+00:00',
                    'routed', 1, 'cloudflare')"""
        ).lastrowid
        conn.execute(
            """INSERT INTO custom_domain_cloudflare_diagnostics
            (claim_id, route_generation, checked_at, dns_status, edge_tls_status,
             edge_http_status, http_forward_status, origin_status)
            VALUES (?, 1, '2026-07-16T00:00:00+00:00', 'healthy', 'healthy',
                    'healthy', 'healthy', 'healthy')""",
            (claim_id,),
        )

    Database(path).init()

    with sqlite3.connect(path) as conn:
        row = conn.execute(
            """SELECT ownership_status, ownership_error, consecutive_failures
            FROM custom_domain_cloudflare_diagnostics WHERE claim_id = ?""",
            (claim_id,),
        ).fetchone()
    assert row == ("not_checked", None, 0)


def test_mode_transition_migration_preserves_claims_and_diagnostics(tmp_path):
    path = tmp_path / "data.db"
    with sqlite3.connect(path) as conn:
        for migration in MIGRATIONS[:7]:
            migration(conn)
        conn.execute("PRAGMA user_version = 7")
        conn.execute("INSERT INTO sites (name) VALUES ('existing-site')")
        claim_id = conn.execute(
            """INSERT INTO custom_domain_claims
            (hostname, site_name, verification_token, status, created_at, expires_at,
             route_status, route_generation, claim_mode, activated_at)
            VALUES ('one.example.com', 'existing-site', 'token-one', 'verified',
                    '2026-07-16T00:00:00+00:00', '2099-07-17T00:00:00+00:00',
                    'routed', 3, 'cloudflare', '2026-07-16T01:00:00+00:00')"""
        ).lastrowid
        conn.execute(
            """INSERT INTO custom_domain_claims
            (hostname, site_name, verification_token, status, created_at, expires_at,
             route_status, route_generation, claim_mode, activated_at)
            VALUES ('direct.example.com', 'existing-site', 'token-direct', 'verified',
                    '2026-07-16T00:00:00+00:00', '2099-07-17T00:00:00+00:00',
                    'routed', 2, 'direct', '2026-07-16T01:00:00+00:00')"""
        )
        conn.execute(
            """INSERT INTO custom_domain_claims
            (hostname, site_name, verification_token, status, created_at, expires_at,
             route_status, route_generation, claim_mode, removal_requested_at)
            VALUES ('removing.example.com', 'existing-site', 'token-removing', 'verified',
                    '2026-07-16T00:00:00+00:00', '2099-07-17T00:00:00+00:00',
                    'removing', 4, 'direct', '2026-07-16T03:00:00+00:00')"""
        )
        conn.execute(
            """INSERT INTO custom_domain_claims
            (hostname, site_name, verification_token, status, created_at, expires_at,
             route_status, route_generation, claim_mode)
            VALUES ('detached.example.com', NULL, 'token-detached', 'cancelled',
                    '2026-07-16T00:00:00+00:00', '2099-07-17T00:00:00+00:00',
                    'removed', 5, 'direct')"""
        )
        conn.execute(
            """INSERT INTO custom_domain_cloudflare_diagnostics
            (claim_id, route_generation, checked_at, dns_status, edge_tls_status,
             edge_http_status, http_forward_status, origin_status, ownership_status)
            VALUES (?, 3, '2026-07-16T02:00:00+00:00', 'healthy', 'healthy',
                    'healthy', 'healthy', 'healthy', 'healthy')""",
            (claim_id,),
        )

    Database(path).init()

    with sqlite3.connect(path) as conn:
        claim = conn.execute(
            """SELECT claim_mode, activated_at, mode_generation, health_checked_at
            FROM custom_domain_claims"""
        ).fetchone()
        diagnostic = conn.execute(
            """SELECT route_generation, mode_generation, probe_generation,
                      answer_fingerprint
               FROM custom_domain_cloudflare_diagnostics"""
        ).fetchone()
        transition_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(custom_domain_mode_transitions)")
        }
        preserved = conn.execute(
            """SELECT hostname, site_name, route_status, claim_mode, activated_at
            FROM custom_domain_claims ORDER BY id"""
        ).fetchall()

    assert claim[:3] == ("cloudflare", "2026-07-16T01:00:00+00:00", 0)
    assert claim[3] is not None
    assert diagnostic == (3, 0, 0, None)
    assert preserved == [
        (
            "one.example.com",
            "existing-site",
            "routed",
            "cloudflare",
            "2026-07-16T01:00:00+00:00",
        ),
        (
            "direct.example.com",
            "existing-site",
            "routed",
            "direct",
            "2026-07-16T01:00:00+00:00",
        ),
        ("removing.example.com", "existing-site", "removing", "direct", None),
        ("detached.example.com", None, "removed", "direct", None),
    ]
    assert {
        "claim_id",
        "mode_generation",
        "probe_generation",
        "source_mode",
        "target_mode",
        "state",
        "answer_fingerprint",
        "confirmed_fingerprint",
        "confirmed_at",
        "lease_owner",
        "lease_expires_at",
    } <= transition_columns


def test_transition_schema_rejects_stale_mode_generation(tmp_path):
    path = tmp_path / "data.db"
    Database(path).init()
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO sites (name) VALUES ('site')")
        claim_id = conn.execute(
            """INSERT INTO custom_domain_claims
            (hostname, site_name, verification_token, status, created_at, expires_at,
             route_status, route_generation)
            VALUES ('one.example.com', 'site', 'token', 'verified', 'now', 'later',
                    'routed', 1)"""
        ).lastrowid
        with pytest.raises(sqlite3.IntegrityError, match="mode generation"):
            conn.execute(
                """INSERT INTO custom_domain_mode_transitions
                (claim_id, mode_generation, target_mode, state, started_at)
                VALUES (?, 99, 'direct', 'observing', 'now')""",
                (claim_id,),
            )


def test_retarget_migration_marks_existing_automatic_onboarding(tmp_path):
    path = tmp_path / "data.db"
    with sqlite3.connect(path) as conn:
        for migration in MIGRATIONS[:10]:
            migration(conn)
        conn.execute("PRAGMA user_version = 10")
        conn.execute("INSERT INTO sites (name) VALUES ('site')")
        claim_id = conn.execute(
            """INSERT INTO custom_domain_claims
            (hostname, site_name, verification_token, status, created_at, expires_at,
             route_status, route_generation, claim_mode, mode_generation,
             automatic_mode)
            VALUES ('one.example.com', 'site', 'token', 'verified', 'now', 'later',
                    'routed', 1, 'cloudflare', 5, 1)"""
        ).lastrowid
        conn.execute(
            """INSERT INTO custom_domain_mode_transitions
            (claim_id, mode_generation, probe_generation, source_mode, target_mode,
             state, started_at)
            VALUES (?, 5, 2, NULL, 'cloudflare', 'observing', 'now')""",
            (claim_id,),
        )

    Database(path).init()

    with sqlite3.connect(path) as conn:
        automatic_retarget = conn.execute(
            """SELECT automatic_retarget FROM custom_domain_mode_transitions
            WHERE claim_id = ?""",
            (claim_id,),
        ).fetchone()[0]
    assert automatic_retarget == 1


def test_migrations_are_idempotent(tmp_path):
    path = tmp_path / "data.db"

    Database(path).init()
    Database(path).init()

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)


def test_normal_connections_enforce_foreign_keys(tmp_path):
    path = tmp_path / "data.db"
    Database(path).init()

    with pytest.raises(sqlite3.IntegrityError):
        with Database(path).connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, user_id, expires_at) VALUES ('session', 999, '2099-01-01')"
            )


def test_existing_foreign_key_violation_blocks_startup(tmp_path):
    path = tmp_path / "data.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            PRAGMA foreign_keys = OFF;
            CREATE TABLE users (id INTEGER PRIMARY KEY);
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            INSERT INTO sessions (id, user_id, expires_at) VALUES ('orphan', 999, '2099-01-01');
        """)

    with pytest.raises(RuntimeError, match="foreign-key violations"):
        Database(path).init()


def test_site_level_access_migration_widens_existing_policies(tmp_path):
    """A legacy path-scoped policy must widen to the whole site, never narrow."""
    path = tmp_path / "data.db"
    conn = sqlite3.connect(path)
    _configure_connection(conn)
    site_level_version = next(
        version
        for version, migration in enumerate(MIGRATIONS, start=1)
        if migration.__name__ == "_buzz_access_site_level"
    )
    for version, migration in enumerate(MIGRATIONS[: site_level_version - 1], start=1):
        migration(conn)
        conn.execute(f"PRAGMA user_version = {version}")
    conn.execute("INSERT INTO users (id, github_id, github_login) VALUES (1, 5, 'owner')")
    conn.execute("INSERT INTO sites (name, owner_id, size_bytes) VALUES ('legacy', 1, 1)")
    policy_id = conn.execute(
        "INSERT INTO site_access_policies (site_name) VALUES ('legacy')"
    ).lastrowid
    conn.execute(
        "INSERT INTO site_access_patterns (policy_id, position, pattern) VALUES (?, 0, '/admin/**')",
        (policy_id,),
    )
    conn.execute(
        "INSERT INTO sessions (id, user_id, expires_at) VALUES ('s', 1, '9999-01-01')"
    )
    conn.execute(
        "INSERT INTO site_access_grants "
        "(id, policy_id, generation, user_id, session_id, hostname, expires_at) "
        "VALUES ('g', ?, 1, 1, 's', 'legacy.example.com', '9999-01-01')",
        (policy_id,),
    )
    conn.commit()
    conn.close()

    Database(path).init()

    with Database(path).connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM site_access_policies WHERE site_name = 'legacy'"
        ).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM site_access_grants").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'site_access_patterns'"
        ).fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def _database_at_version(path, version):
    with sqlite3.connect(path) as conn:
        for migration in MIGRATIONS[:version]:
            migration(conn)
        conn.execute(f"PRAGMA user_version = {version}")
        conn.execute("INSERT INTO sites (name) VALUES ('existing-site')")


def test_fresh_database_takes_no_backup(tmp_path):
    database = Database(tmp_path / "data.db")

    database.init()

    assert database.backups.existing() == []
    assert not (tmp_path / "backups").exists()


def test_current_database_takes_no_backup(tmp_path):
    database = Database(tmp_path / "data.db")
    database.init()

    database.init()

    assert database.backups.existing() == []


def test_pending_migrations_back_up_the_pre_migration_database(tmp_path):
    path = tmp_path / "data.db"
    previous_version = len(MIGRATIONS) - 1
    _database_at_version(path, previous_version)
    database = Database(path)

    database.init()

    [backup] = database.backups.existing()
    assert backup.path.parent == tmp_path / "backups"
    assert backup.path.name.startswith(f"data-schema{previous_version}-")
    assert backup.schema_version == previous_version
    with sqlite3.connect(backup.path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == previous_version
        assert conn.execute("SELECT name FROM sites").fetchall() == [("existing-site",)]
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    assert not list((tmp_path / "backups").glob("*.partial"))


def test_backups_are_readable_only_by_the_owner(tmp_path):
    path = tmp_path / "data.db"
    _database_at_version(path, len(MIGRATIONS) - 1)
    database = Database(path)

    database.init()

    [backup] = database.backups.existing()
    assert stat.S_IMODE(backup.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(backup.path.parent.stat().st_mode) == 0o700


def test_only_the_two_newest_backups_are_kept(tmp_path):
    path = tmp_path / "data.db"
    _database_at_version(path, len(MIGRATIONS) - 1)
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    for name in (
        "data-schema14-20260101T000000Z.db",
        "data-schema15-20260201T000000Z.db",
        "unrelated.db",
    ):
        (backups_dir / name).write_bytes(b"")
    database = Database(path)

    database.init()

    kept = [backup.path.name for backup in database.backups.existing()]
    assert len(kept) == 2
    assert kept[0].startswith(f"data-schema{len(MIGRATIONS) - 1}-")
    assert kept[1] == "data-schema15-20260201T000000Z.db"
    assert (backups_dir / "unrelated.db").exists()


def test_failed_backup_blocks_migration(tmp_path):
    path = tmp_path / "data.db"
    previous_version = len(MIGRATIONS) - 1
    _database_at_version(path, previous_version)
    (tmp_path / "backups").write_text("not a directory")

    with pytest.raises(RuntimeError, match="Could not back up the database"):
        Database(path).init()

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == previous_version


def test_newer_schema_error_names_the_backup_to_restore(tmp_path):
    path = tmp_path / "data.db"
    Database(path).init()
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    supported = len(MIGRATIONS)
    restorable = backups_dir / f"data-schema{supported}-20261001T000000Z.db"
    restorable.write_bytes(b"")
    (backups_dir / f"data-schema{supported + 1}-20261002T000000Z.db").write_bytes(b"")
    with sqlite3.connect(path) as conn:
        conn.execute(f"PRAGMA user_version = {supported + 2}")

    with pytest.raises(RuntimeError) as error:
        Database(path).init()

    message = str(error.value)
    assert f"schema version {supported + 2} is newer than supported version {supported}" in message
    assert f"restore {restorable} over {path}" in message


def test_newer_schema_error_without_backup_says_so(tmp_path):
    path = tmp_path / "data.db"
    Database(path).init()
    with sqlite3.connect(path) as conn:
        conn.execute(f"PRAGMA user_version = {len(MIGRATIONS) + 1}")

    with pytest.raises(RuntimeError, match="No backup restorable by this version"):
        Database(path).init()
