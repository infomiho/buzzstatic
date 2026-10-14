"""Startup configuration for the custom-domains runtime. Captures the wiring
knobs the runtime reads once when it starts, derived from the resolved
application settings."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..settings import Settings


@dataclass(frozen=True)
class CustomDomainsConfig:
    custom_domains_enabled: bool
    custom_domain_ingress_ips: frozenset[str]
    custom_domain_operator_token: str | None
    custom_domain_origin_host: str | None
    custom_domain_reconcile_seconds: float
    traefik_cert_resolver: str | None
    traefik_dynamic_dir: Path | None
    traefik_https_entrypoint: str
    traefik_service: str

    @classmethod
    def from_settings(cls, settings: Settings) -> "CustomDomainsConfig":
        return cls(
            custom_domains_enabled=settings.custom_domains_enabled,
            custom_domain_ingress_ips=settings.custom_domain_ingress_ips,
            custom_domain_operator_token=settings.custom_domain_operator_token,
            custom_domain_origin_host=settings.custom_domain_origin_host,
            custom_domain_reconcile_seconds=settings.custom_domain_reconcile_seconds,
            traefik_cert_resolver=settings.traefik_cert_resolver,
            traefik_dynamic_dir=settings.traefik_dynamic_dir,
            traefik_https_entrypoint=settings.traefik_https_entrypoint,
            traefik_service=settings.traefik_service,
        )
