"""The custom-domains runtime: one object owning wiring order, the reconcile
loop, startup guards, capabilities, and request-time lookups. The host holds a
single instance and never learns the collaborators behind it."""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Callable

from .activation import DomainActivator
from .capabilities import DomainCapabilities, compute_capabilities
from .claims import DnsTxtResolver, DomainClaimStore
from .cloudflare import CloudflareDiagnostician
from .config import CustomDomainsConfig
from .errors import ClaimNotFound
from .evidence import DomainDnsObserver, DomainEvidenceCollector
from .probes import CloudflareRangeError, CloudflareRangeState, load_cloudflare_ranges
from .operator import OperatorServer
from .routing import DomainRouteReconciler
from ..traefik_file_provider import TraefikDynamicFile
from .traefik import TraefikFileControl, revision_max_age
from .transitions import (
    DomainClaimStateMachine,
    DomainTransitionCoordinator,
    TransitionValidationFailed,
)

logger = logging.getLogger(__name__)

ROUTES_FILE_NAME = "buzz-routes"
ROUTERS_UNMANAGED = "routers_unmanaged"
DOMAIN_CHECK_PREFIX = "/.well-known/buzz-domain-check/"
OPERATOR_PORT = 8081


class CustomDomainsRuntime:
    def __init__(self, config: CustomDomainsConfig, connect: Callable):
        self._config = config
        self._connect = connect
        self.txt_resolver = DnsTxtResolver()
        self.control: TraefikFileControl | None = None
        self._operator_server: OperatorServer | None = None
        self.runtime_ready = False
        self.withdrawing = False
        self.error_code: str | None = None
        self.range_state = CloudflareRangeState(load_error="range_data_missing")
        self.diagnostician: CloudflareDiagnostician | None = None
        self.transition_coordinator: DomainTransitionCoordinator | None = None
        self._reconciler_task: asyncio.Task | None = None
        self._stop_reconciler = asyncio.Event()

    # -- capabilities ----------------------------------------------------

    def capabilities(self) -> DomainCapabilities:
        return compute_capabilities(
            enabled=self._config.custom_domains_enabled,
            control_configured=bool(self._config.traefik_dynamic_dir),
            ingress_ips=self._config.custom_domain_ingress_ips,
            control=self.control,
            diagnostician=self.diagnostician,
            range_state=self.range_state,
            diagnostic_runtime_ready=self.runtime_ready,
            coordinator=self.transition_coordinator,
            withdrawing=self.withdrawing,
        )

    # -- request-time lookups --------------------------------------------

    def resolve_challenge(
        self, hostname: str | None, path: str
    ) -> tuple[int, str, str] | None:
        if (
            not self._serving
            or not hostname
            or not path.startswith(DOMAIN_CHECK_PREFIX)
        ):
            return None
        token = path.removeprefix(DOMAIN_CHECK_PREFIX)
        if not token or "/" in token:
            return None
        with self._connect() as conn:
            store = DomainClaimStore(conn)
            claim = store.find_challenge(hostname.lower().rstrip("."), token)
            if claim:
                store.mark_challenge_seen(claim.id, claim.route_generation)
        if not claim or not claim.site_name:
            return None
        return claim.id, claim.site_name, token

    def activated_site(self, hostname: str | None) -> str | None:
        if not self._serving or not hostname:
            return None
        with self._connect() as conn:
            claim = DomainClaimStore(conn).find_activated(hostname.lower().rstrip("."))
        return claim.site_name if claim else None

    def activated_hostnames_for_site(self, site_name: str) -> frozenset[str]:
        with self._connect() as conn:
            return DomainClaimStore(conn).activated_hostnames_for_site(site_name)

    @property
    def _serving(self) -> bool:
        return self._config.custom_domains_enabled and not self.error_code

    # -- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        config = self._config
        if problem := self._unmanaged_routers_problem():
            self.error_code = ROUTERS_UNMANAGED
            logger.error("Custom domains are offline: %s", problem)
            return
        if not config.traefik_dynamic_dir:
            return
        if config.custom_domains_enabled:
            routing_enabled = True
        elif self._routed_hostnames():
            routing_enabled = False
        else:
            return
        with self._connect() as conn:
            DomainClaimStore(conn).prepare_routes(routing_enabled)
        control = TraefikFileControl(
            TraefikDynamicFile(config.traefik_dynamic_dir, ROUTES_FILE_NAME),
            config.custom_domain_origin_host,
            config.traefik_https_entrypoint,
            config.traefik_service,
            max_age=revision_max_age(config.custom_domain_reconcile_seconds),
        )
        self.control = control
        if not routing_enabled:
            self._start_withdrawal(control)
            return
        self._start_reconciliation(control)
        if config.custom_domain_operator_token:
            self._operator_server = OperatorServer(
                config.custom_domain_operator_token,
                OPERATOR_PORT,
                handoff_provider=self._active_handoffs,
                cancel_provider=self._cancel_operator_transition,
            )
            self._operator_server.start()

    def _start_reconciliation(self, control: TraefikFileControl) -> None:
        config = self._config
        self.runtime_ready = True
        try:
            transition_ranges = load_cloudflare_ranges()
            transition_range_error = None
        except CloudflareRangeError as exc:
            transition_ranges = None
            transition_range_error = exc.code
        self.range_state = CloudflareRangeState(transition_ranges, transition_range_error)
        transition_observer = DomainDnsObserver(
            ingress_addresses=config.custom_domain_ingress_ips,
            cloudflare_range_state=self.range_state,
        )
        reconciler = DomainRouteReconciler(
            control,
            config.traefik_https_entrypoint,
            config.traefik_service,
            config.traefik_cert_resolver,
            routing_enabled=lambda: True,
            connect=self._connect,
        )

        def validate_transition_router(claim) -> None:
            loaded = control.loaded_routers()
            if loaded is None:
                raise TransitionValidationFailed(
                    "routing_config_not_loaded", transient=True
                )
            if claim.route_name not in loaded:
                raise TransitionValidationFailed("router_not_observed")

        evidence_collector = DomainEvidenceCollector(
            transition_observer,
            config.custom_domain_origin_host,
            validate_transition_router,
            cloudflare_range_state=self.range_state,
        )
        activator = DomainActivator(
            evidence_collector=evidence_collector, connect=self._connect
        )
        self.diagnostician = CloudflareDiagnostician(
            evidence_collector,
            activation_enabled=True,
            range_state=self.range_state,
            connect=self._connect,
        )
        self.transition_coordinator = DomainTransitionCoordinator(
            evidence_collector,
            self.diagnostician,
            admission_enabled=lambda: self.capabilities().automatic_ready,
            cloudflare_target_enabled=lambda: self.capabilities().cloudflare_ready,
            database=self._connect,
        )
        diagnostician = self.diagnostician
        coordinator = self.transition_coordinator

        def reconcile_once() -> None:
            reconciler.run_once()
            coordinator.run_once()
            activator.run_once()
            diagnostician.run_once()

        self._run_periodically(reconcile_once)

    def _start_withdrawal(self, control: TraefikFileControl) -> None:
        """Custom domains are disabled but routers may still be published:
        rewrite the routes file without them and wait for Traefik to load it."""
        config = self._config
        reconciler = DomainRouteReconciler(
            control,
            config.traefik_https_entrypoint,
            config.traefik_service,
            config.traefik_cert_resolver,
            routing_enabled=lambda: False,
            connect=self._connect,
        )
        self.withdrawing = True
        remaining = self._routed_hostnames()
        logger.warning(
            "Custom domains are disabled. Withdrawing Traefik routers for: %s",
            ", ".join(remaining),
        )

        def withdraw_once() -> None:
            nonlocal remaining
            if not remaining:
                return
            reconciler.run_once()
            remaining = self._routed_hostnames()
            if not remaining:
                self.withdrawing = False
                logger.warning("All custom-domain routers are withdrawn")

        self._run_periodically(withdraw_once)

    def _run_periodically(self, run_once: Callable[[], None]) -> None:
        interval_seconds = self._config.custom_domain_reconcile_seconds

        async def run() -> None:
            while not self._stop_reconciler.is_set():
                started_at = asyncio.get_running_loop().time()
                try:
                    await asyncio.to_thread(run_once)
                except Exception:
                    logger.exception("Custom domain reconciliation failed")
                try:
                    elapsed = asyncio.get_running_loop().time() - started_at
                    interval = random.uniform(0.8, 1.2) * interval_seconds
                    await asyncio.wait_for(
                        self._stop_reconciler.wait(),
                        timeout=max(0, interval - elapsed),
                    )
                except TimeoutError:
                    pass

        self._reconciler_task = asyncio.create_task(run())

    async def stop(self) -> None:
        if self._reconciler_task:
            self._stop_reconciler.set()
            try:
                await self._reconciler_task
            except Exception:
                logger.exception("Custom domain reconciler shutdown failed")
            self._reconciler_task = None
        try:
            if self._operator_server:
                self._operator_server.stop()
        finally:
            self._operator_server = None
            self.control = None
            self.runtime_ready = False
            self.withdrawing = False
            self.transition_coordinator = None

    # -- internal --------------------------------------------------------

    def _unmanaged_routers_problem(self) -> str | None:
        """Routers that still exist in the database must stay withdrawable, which
        needs the routes file. Without it Buzz keeps hosting canonical sites but
        serves no custom domain, since Traefik may still route them to Buzz."""
        config = self._config
        if config.traefik_dynamic_dir:
            return None
        with self._connect() as conn:
            store = DomainClaimStore(conn)
            routed_cloudflare_claim = store.has_routed_cloudflare_claim()
            routed_hostnames = store.routed_hostnames()
        if routed_cloudflare_claim:
            return (
                "active Cloudflare domains require the complete custom-domain runtime. "
                "Set BUZZ_TRAEFIK_DYNAMIC_DIR and redeploy."
            )
        if routed_hostnames and not config.custom_domains_enabled:
            return (
                "custom domains are disabled but Traefik may still route these "
                f"hostnames to Buzz: {', '.join(routed_hostnames)}. "
                "Buzz withdraws them when it starts with BUZZ_CUSTOM_DOMAINS_ENABLED=false "
                "and BUZZ_TRAEFIK_DYNAMIC_DIR set. Restore BUZZ_TRAEFIK_DYNAMIC_DIR and "
                "redeploy."
            )
        return None

    def _routed_hostnames(self) -> list[str]:
        with self._connect() as conn:
            return DomainClaimStore(conn).routed_hostnames()

    def _active_handoffs(self) -> list[dict]:
        with self._connect() as conn:
            return DomainClaimStateMachine(conn).active_handoffs()

    def _cancel_operator_transition(self, claim_id: int) -> dict:
        with self._connect() as conn:
            site_name = DomainClaimStore(conn).site_name_for(claim_id)
        if not site_name:
            raise ClaimNotFound("Custom domain claim not found")
        self.transition_coordinator.cancel(claim_id, site_name)
        return {"claim_id": claim_id, "state": "cancelled"}
