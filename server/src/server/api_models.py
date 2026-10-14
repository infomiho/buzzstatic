from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    detail: str


class CertificateHealthResponse(BaseModel):
    ok: bool
    reason: Literal["proxy_unreachable", "certificate_invalid", "certificate_expiring"] | None
    detail: str | None
    expires_at: datetime | None
    checked_at: datetime


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"] = Field(
        description="`degraded` reports a problem that does not stop Buzz from serving.",
    )
    wildcard_certificate: CertificateHealthResponse | None = Field(
        default=None,
        description="Latest check of the certificate the proxy serves for site subdomains.",
    )
    wildcard_certificate_error: Literal[
        "not_configured", "issuance_failed", "storage_failed", "publish_failed", "check_failed"
    ] | None = Field(
        default=None,
        description=(
            "Why Buzz cannot obtain, save or publish that certificate. The server log has the "
            "detail. Buzz retries after 2, 5, 15 and 30 minutes, then every 30 minutes."
        ),
    )
    custom_domains_error: Literal["routers_unmanaged"] | None = Field(
        default=None,
        description=(
            "Custom-domain routers exist but `BUZZ_TRAEFIK_DYNAMIC_DIR` is unset, so Buzz "
            "serves no custom domain until it is set. The server log has the detail."
        ),
    )


class VersionResponse(BaseModel):
    version: str
    min_cli_version: str


class DeviceAuthorizationResponse(BaseModel):
    device_code: str = Field(json_schema_extra={"writeOnly": True})
    user_code: str
    verification_uri: str
    interval: int
    expires_in: int


class DevicePollRequest(BaseModel):
    device_code: str = Field(json_schema_extra={"writeOnly": True})


class ApiUser(BaseModel):
    login: str
    name: str | None


class DevicePollPendingResponse(BaseModel):
    status: Literal["pending"]
    interval: int | None = None


class DevicePollCompleteResponse(BaseModel):
    status: Literal["complete"]
    token: str = Field(json_schema_extra={"writeOnly": True})
    user: ApiUser


class LogoutResponse(BaseModel):
    success: Literal[True]


class DeploySiteResponse(BaseModel):
    name: str
    site_name: str
    url: str
    private: bool
    deployment_number: int


class SiteDeploymentResponse(BaseModel):
    deployment_number: int
    deployed_at: datetime
    size_bytes: int
    source: Literal["dashboard", "api"]
    actor: str
    credential: str | None
    active: bool


class SiteResponse(BaseModel):
    name: str
    created: datetime
    last_deployed_at: datetime
    size_bytes: int | None
    total_views: int
    private: bool


class SiteAccessResponse(BaseModel):
    private: bool


class AddSiteReaderRequest(BaseModel):
    github_login: str


class SiteReaderResponse(BaseModel):
    id: int
    github_login: str
    github_name: str | None
    avatar_url: str | None


class GitHubUserResponse(BaseModel):
    github_id: int
    github_login: str
    github_name: str | None
    avatar_url: str | None


class CreateDomainClaimRequest(BaseModel):
    hostname: str


class DomainVerificationRecord(BaseModel):
    type: Literal["TXT"]
    name: str
    value: str


class DomainClaimResponse(BaseModel):
    id: int
    hostname: str
    site_name: str | None
    status: Literal["pending", "verified", "expired", "cancelled"]
    verification: DomainVerificationRecord
    created_at: str
    expires_at: str
    verified_at: str | None
    last_checked_at: str | None
    last_error: str | None
    route_status: Literal["not_routed", "publishing", "routed", "removing", "removed"]
    route_generation: int
    route_error: str | None
    challenge_path: str | None
    challenge_seen_at: str | None
    activated_at: str | None
    activation_checked_at: str | None
    activation_error: str | None
    removal_requested_at: str | None
    withdrawn_at: str | None
    mode: Literal["direct", "cloudflare"]
    effective_mode: Literal["direct", "cloudflare"] | None
    observed_mode: Literal["direct", "cloudflare", "mixed", "unsupported", "unavailable"] | None
    target_mode: Literal["direct", "cloudflare"] | None
    connection_status: Literal[
        "waiting_for_dns", "securing", "connected", "updating", "action_needed"
    ]
    transition_started_at: str | None
    transition_deadline_at: str | None
    transition_error: str | None
    cloudflare_diagnostics: "CloudflareDiagnosticResponse | None"


class CloudflareDiagnosticComponent(BaseModel):
    status: str
    error: str | None


class CloudflareEdgeHttpDiagnostic(CloudflareDiagnosticComponent):
    status_code: int | None
    address: str | None
    cf_ray: str | None
    cf_cache_status: str | None
    redirect_location: str | None


class CloudflareHttpForwardDiagnostic(CloudflareDiagnosticComponent):
    status_code: int | None


class CloudflareDiagnosticResponse(BaseModel):
    generation: int
    checked_at: str
    ranges_version: str | None
    ownership: CloudflareDiagnosticComponent
    dns: CloudflareDiagnosticComponent
    edge_tls: CloudflareDiagnosticComponent
    edge_http: CloudflareEdgeHttpDiagnostic
    http_forwarding: CloudflareHttpForwardDiagnostic
    origin: CloudflareDiagnosticComponent
    consecutive_failures: int


class CustomDomainRoutingTarget(BaseModel):
    type: Literal["A", "AAAA"]
    value: str


class CustomDomainCapabilityResponse(BaseModel):
    status: Literal["disabled", "unready", "ready"]
    detail: str | None
    enabled: bool
    control_ready: bool
    routing_enabled: bool
    routing_targets: list[CustomDomainRoutingTarget]
    automatic: "AutomaticDomainTransitionCapability"
    cloudflare: "CloudflareCapability"


class AutomaticDomainTransitionCapability(BaseModel):
    ready: bool
    detail: str | None


class CloudflareCapability(BaseModel):
    supported: bool
    detail: str | None


class CreateTokenRequest(BaseModel):
    site_name: str
    name: str = Field(default="Deployment token", min_length=1, max_length=100)


class DeploymentTokenResponse(BaseModel):
    id: str
    name: str
    site_name: str
    created_at: str
    expires_at: str | None
    last_used_at: str | None


class CreatedDeploymentTokenResponse(BaseModel):
    id: str
    token: str = Field(json_schema_extra={"writeOnly": True})
    name: str
    site_name: str
