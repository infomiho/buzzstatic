from __future__ import annotations

import logging
from collections.abc import Callable

from ..https_redirect import MIDDLEWARE as HTTPS_REDIRECT_MIDDLEWARE
from ..https_redirect import NOOP_SERVICE
from .claims import DomainClaim, DomainClaimStore
from .traefik import TraefikFileControl

logger = logging.getLogger(__name__)


def http_route_name(claim: DomainClaim) -> str:
    return f"{claim.route_name}-http"


def http_redirect_router(claim: DomainClaim, http_entrypoint: str) -> dict:
    """Uses the redirect middleware Buzz publishes in `buzz-http.yaml`."""
    return {
        "entryPoints": [http_entrypoint],
        "rule": f"Host(`{claim.hostname}`)",
        "middlewares": [HTTPS_REDIRECT_MIDDLEWARE],
        "service": NOOP_SERVICE,
    }


def custom_domain_routers(
    claims: list[DomainClaim],
    https_entrypoint: str,
    http_entrypoint: str,
    service: str,
    cert_resolver: str,
) -> dict[str, dict]:
    routers = {}
    for claim in claims:
        routers[claim.route_name] = {
            "entryPoints": [https_entrypoint],
            "rule": f"Host(`{claim.hostname}`)",
            "service": service,
            "tls": {"certResolver": cert_resolver},
        }
        routers[http_route_name(claim)] = http_redirect_router(claim, http_entrypoint)
    return routers


class DomainRouteReconciler:
    """Writes routable claims to Traefik's dynamic file, then acknowledges
    publication and withdrawal only once Traefik serves exactly that file."""

    def __init__(
        self,
        control: TraefikFileControl,
        https_entrypoint: str,
        http_entrypoint: str,
        service: str,
        cert_resolver: str,
        routing_enabled: Callable[[], bool],
        connect: Callable,
    ):
        self._control = control
        self._https_entrypoint = https_entrypoint
        self._http_entrypoint = http_entrypoint
        self._service = service
        self._cert_resolver = cert_resolver
        self._routing_enabled = routing_enabled
        self._connect = connect

    def run_once(self) -> None:
        with self._connect() as conn:
            store = DomainClaimStore(conn)
            claims = store.prepare_routes(self._routing_enabled())
            routable = store.routable_claims()
        try:
            self._control.publish(
                custom_domain_routers(
                    routable,
                    self._https_entrypoint,
                    self._http_entrypoint,
                    self._service,
                    self._cert_resolver,
                )
            )
        except Exception:
            logger.exception("Could not write Traefik dynamic configuration")
        self._control.refresh()
        loaded = self._control.loaded_routers()
        for claim in claims:
            try:
                if claim.route_status == "publishing":
                    self._reconcile_publication(claim, loaded)
                elif claim.route_status == "removing":
                    self._reconcile_withdrawal(claim, loaded)
            except Exception:
                logger.exception("Custom domain router %s reconciliation failed", claim.route_name)

    def _reconcile_publication(
        self, claim: DomainClaim, loaded: frozenset[str] | None
    ) -> None:
        if loaded is None:
            self._record_error(claim, "routing_config_not_loaded")
            return
        if claim.route_name not in loaded:
            self._record_error(claim, "router_not_observed")
            return
        with self._connect() as conn:
            DomainClaimStore(conn).mark_routed(claim.id, claim.route_generation)
        logger.info("Custom domain router %s acknowledged", claim.route_name)

    def _reconcile_withdrawal(
        self, claim: DomainClaim, loaded: frozenset[str] | None
    ) -> None:
        if loaded is None:
            self._record_error(claim, "routing_config_not_loaded")
            return
        if claim.route_name in loaded:
            self._record_error(claim, "router_still_present")
            return
        with self._connect() as conn:
            DomainClaimStore(conn).finish_withdrawal(claim.id, claim.route_generation)
        logger.info("Custom domain router %s withdrawal acknowledged", claim.route_name)

    def _record_error(self, claim: DomainClaim, error: str) -> None:
        with self._connect() as conn:
            changed = DomainClaimStore(conn).record_route_error(
                claim.id,
                claim.route_generation,
                error,
            )
        if changed:
            logger.warning("Custom domain router %s: %s", claim.route_name, error)
