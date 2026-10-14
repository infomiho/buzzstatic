import pytest

from server.custom_domains.claims import DomainClaimStore
from server.custom_domains.transitions import DomainClaimStateMachine
from server.custom_domains.routing import DomainRouteReconciler, custom_domain_routers


@pytest.fixture
def routing_db(database):
    with database.connect() as conn:
        conn.execute(
            "INSERT INTO users (id, github_id, github_login) VALUES (1, 1, 'alice')"
        )
        conn.execute("INSERT INTO sites (name, owner_id) VALUES ('my-site', 1)")
        store = DomainClaimStore(conn)
        claim = store.create("my-site", "www.example.com")
        store.record_check(claim.id, "my-site", (claim.verification_value,))
    return database.connect


class FakeControl:
    """Traefik serves what Buzz last published unless a test says otherwise."""

    def __init__(self, serves=lambda published: published, fail_write=False):
        self._serves = serves
        self._fail_write = fail_write
        self.published: frozenset[str] = frozenset()
        self.writes = []

    def publish(self, routers, certificates=()):
        if self._fail_write:
            raise OSError("read-only file system")
        self.writes.append(routers)
        self.published = frozenset(routers)

    def refresh(self):
        pass

    def loaded_routers(self):
        return self._serves(self.published)


def reconciler(connect, control, enabled=True):
    return DomainRouteReconciler(
        control,
        "https",
        "buzz@docker",
        "buzz-custom",
        routing_enabled=lambda: enabled,
        connect=connect,
    )


def claims(connect):
    with connect() as conn:
        return DomainClaimStore(conn).list_for_site("my-site")


def test_routers_are_exact_and_deterministic(routing_db):
    with routing_db() as conn:
        claim = DomainClaimStore(conn).prepare_routes(True)[0]

    assert custom_domain_routers([claim], "https", "buzz@docker", "buzz-custom") == {
        claim.route_name: {
            "entryPoints": ["https"],
            "rule": "Host(`www.example.com`)",
            "service": "buzz@docker",
            "tls": {"certResolver": "buzz-custom"},
        }
    }


def test_reconciler_publishes_every_routable_alias(routing_db):
    with routing_db() as conn:
        store = DomainClaimStore(conn)
        for hostname in ("two.example.com", "three.example.com"):
            claim = store.create("my-site", hostname)
            store.record_check(claim.id, "my-site", (claim.verification_value,))
    control = FakeControl()

    reconciler(routing_db, control).run_once()

    routers = control.writes[-1]
    assert set(routers) == {claim.route_name for claim in claims(routing_db)}
    assert {router["rule"] for router in routers.values()} == {
        "Host(`www.example.com`)",
        "Host(`two.example.com`)",
        "Host(`three.example.com`)",
    }


def test_reconciler_acknowledges_router_in_loaded_revision(routing_db):
    reconciler(routing_db, FakeControl()).run_once()

    [claim] = claims(routing_db)
    assert claim.route_status == "routed"
    assert claim.route_error is None


def test_publication_waits_until_traefik_serves_the_written_revision(routing_db):
    reconciler(routing_db, FakeControl(serves=lambda _published: None)).run_once()

    [claim] = claims(routing_db)
    assert claim.route_status == "publishing"
    assert claim.route_error == "routing_config_not_loaded"


def test_publication_requires_router_in_loaded_revision(routing_db):
    reconciler(routing_db, FakeControl(serves=lambda _published: frozenset())).run_once()

    [claim] = claims(routing_db)
    assert claim.route_status == "publishing"
    assert claim.route_error == "router_not_observed"


def test_failed_write_does_not_acknowledge_publication(routing_db):
    control = FakeControl(fail_write=True)

    reconciler(routing_db, control).run_once()

    [claim] = claims(routing_db)
    assert claim.route_status == "publishing"
    assert claim.route_error == "router_not_observed"


@pytest.mark.parametrize("serves", [lambda _published: None, lambda _published: frozenset()])
def test_route_reconciler_does_not_duplicate_active_claim_health_checks(routing_db, serves):
    with routing_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        conn.execute(
            "UPDATE custom_domain_claims SET claim_mode = 'cloudflare' WHERE id = ?",
            (claim.id,),
        )
    reconciler(routing_db, FakeControl()).run_once()
    with routing_db() as conn:
        claim = DomainClaimStore(conn).list_for_site("my-site")[0]
        DomainClaimStateMachine(conn).apply_activation_decision(claim, None)

    reconciler(routing_db, FakeControl(serves=serves)).run_once()

    [claim] = claims(routing_db)
    assert claim.activated_at is not None
    assert claim.route_error is None
    assert claim.activation_error is None


def test_user_removal_waits_for_loaded_revision_without_router(routing_db):
    reconciler(routing_db, FakeControl()).run_once()
    with routing_db() as conn:
        store = DomainClaimStore(conn)
        claim = store.list_for_site("my-site")[0]
        assert store.cancel(claim.id, "my-site") is True
    control = FakeControl()

    reconciler(routing_db, control).run_once()

    [claim] = claims(routing_db)
    assert control.writes == [{}]
    assert claim.status == "cancelled"
    assert claim.route_status == "removed"
    assert claim.withdrawn_at is not None


def test_withdrawal_waits_until_traefik_serves_the_written_revision(routing_db):
    reconciler(routing_db, FakeControl()).run_once()
    with routing_db() as conn:
        store = DomainClaimStore(conn)
        store.cancel(store.list_for_site("my-site")[0].id, "my-site")

    reconciler(routing_db, FakeControl(serves=lambda _published: None)).run_once()

    [claim] = claims(routing_db)
    assert claim.status == "verified"
    assert claim.route_status == "removing"
    assert claim.route_error == "routing_config_not_loaded"


def test_withdrawal_rejects_loaded_revision_that_still_has_router(routing_db):
    reconciler(routing_db, FakeControl()).run_once()
    [routed] = claims(routing_db)
    with routing_db() as conn:
        DomainClaimStore(conn).cancel(routed.id, "my-site")

    stale = FakeControl(serves=lambda _published: frozenset({routed.route_name}))
    reconciler(routing_db, stale).run_once()

    [claim] = claims(routing_db)
    assert claim.route_status == "removing"
    assert claim.route_error == "router_still_present"


def test_operator_disable_withdraws_without_cancelling_ownership(routing_db):
    reconciler(routing_db, FakeControl()).run_once()

    reconciler(routing_db, FakeControl(), enabled=False).run_once()

    [claim] = claims(routing_db)
    assert claim.status == "verified"
    assert claim.route_status == "not_routed"
    assert claim.withdrawn_at is not None


def test_public_challenge_is_recorded_for_current_generation(routing_db):
    with routing_db() as conn:
        store = DomainClaimStore(conn)
        claim = store.prepare_routes(True)[0]
        found = store.find_challenge(claim.hostname, claim.challenge_token)
        store.mark_challenge_seen(claim.id, claim.route_generation)

    with routing_db() as conn:
        updated = DomainClaimStore(conn).get(claim.id, "my-site")
    assert found is not None
    assert updated.challenge_seen_at is not None


def test_new_route_generation_resets_public_challenge_evidence(routing_db):
    with routing_db() as conn:
        store = DomainClaimStore(conn)
        first = store.prepare_routes(True)[0]
        store.mark_challenge_seen(first.id, first.route_generation)
        store.mark_routed(first.id, first.route_generation)
        removing = store.prepare_routes(False)[0]
        store.finish_withdrawal(removing.id, removing.route_generation)
        second = store.prepare_routes(True)[0]

    assert second.route_generation == first.route_generation + 1
    assert second.challenge_token != first.challenge_token
    assert second.challenge_seen_at is None
