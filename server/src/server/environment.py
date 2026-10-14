from __future__ import annotations

import os
import ipaddress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


class InvalidEnvironment(ValueError):
    pass


@dataclass(frozen=True)
class EnvironmentVariable:
    name: str
    description: str
    default: Any = None
    required: str | None = None
    sensitive: bool = False
    example: str | None = None
    parser: Callable[[str], Any] = str

    def read(self) -> Any:
        value = os.environ.get(self.name)
        if not value:
            return self.default
        try:
            return self.parser(value)
        except ValueError as exc:
            raise InvalidEnvironment(f"Invalid {self.name}: {exc}") from exc


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Expected a boolean value, got {value!r}")


def parse_positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise ValueError(f"Expected a positive integer, got {value!r}")
    return parsed


def parse_github_logins(value: str) -> frozenset[str]:
    return frozenset(login.strip().lower() for login in value.split(",") if login.strip())


def parse_public_ips(value: str) -> frozenset[str]:
    addresses = set()
    for raw in value.split(","):
        if not raw.strip():
            continue
        address = ipaddress.ip_address(raw.strip())
        if not address.is_global:
            raise ValueError(f"Expected a public ingress IP address, got {address}")
        addresses.add(str(address))
    return frozenset(addresses)


PACKAGE_DIR = Path(__file__).parent.resolve()

ENVIRONMENT_VARIABLES = (
    EnvironmentVariable(
        "BUZZ_PORT",
        "Port used by the Buzz server. The `--port` option takes precedence.",
        default=8080,
        example="8080",
        parser=int,
    ),
    EnvironmentVariable(
        "BUZZ_DATA_DIR",
        "Directory containing deployed sites and the SQLite database.",
        default=str(PACKAGE_DIR),
        example="/data",
    ),
    EnvironmentVariable(
        "BUZZ_DOMAIN",
        "Bare hostname for the dashboard and site subdomains, without a scheme, port, or path.",
        required="Required unless the server runs with --dev.",
        example="buzz.example.com",
    ),
    EnvironmentVariable(
        "GITHUB_CLIENT_ID",
        "Client ID for the GitHub OAuth app used for dashboard sign-in.",
        required="Required unless the server runs with --dev.",
        example="Iv1.example",
    ),
    EnvironmentVariable(
        "GITHUB_CLIENT_SECRET",
        "Client secret for the GitHub OAuth app.",
        required="Required unless the server runs with --dev.",
        sensitive=True,
        example="your-github-client-secret",
    ),
    EnvironmentVariable(
        "BUZZ_ANALYTICS_SECRET",
        "Secret used to hash analytics visitors. Falls back to `GITHUB_CLIENT_SECRET`, then a process-local random value.",
        sensitive=True,
        example="replace-with-a-random-secret",
    ),
    EnvironmentVariable(
        "BUZZ_MAX_ARCHIVE_BYTES",
        "Maximum compressed deployment archive size in bytes.",
        default=500 * 1024 * 1024,
        example="524288000",
        parser=int,
    ),
    EnvironmentVariable(
        "BUZZ_MAX_SITE_BYTES",
        "Maximum extracted size of one deployed site in bytes.",
        default=500 * 1024 * 1024,
        example="524288000",
        parser=int,
    ),
    EnvironmentVariable(
        "BUZZ_MAX_SITE_FILES",
        "Maximum number of files and directories in one deployed site.",
        default=10_000,
        example="10000",
        parser=int,
    ),
    EnvironmentVariable(
        "BUZZ_MAX_ARCHIVE_PATH_BYTES",
        "Maximum UTF-8 byte length of a path in a deployment archive.",
        default=1024,
        example="1024",
        parser=int,
    ),
    EnvironmentVariable(
        "BUZZ_ALLOW_REGISTRATION",
        "Whether new GitHub users can sign up. Existing users keep access when disabled.",
        default=True,
        example="false",
        parser=parse_bool,
    ),
    EnvironmentVariable(
        "BUZZ_ALLOWED_GITHUB_USERS",
        "Comma-separated GitHub usernames allowed to sign in. When set, unlisted users are denied on every request and listed users may sign up even when registration is disabled.",
        example="alice,bob",
        parser=parse_github_logins,
    ),
    EnvironmentVariable(
        "BUZZ_TRAEFIK_DYNAMIC_DIR",
        "Traefik file-provider directory mounted into the Buzz container. Buzz writes the wildcard certificate to `buzz-certificate.yaml` and custom-domain routers to `buzz-routes.yaml` there.",
        example="/traefik-dynamic",
        parser=Path,
    ),
    EnvironmentVariable(
        "BUZZ_CLOUDFLARE_DNS_API_TOKEN",
        "Cloudflare API token with Zone:Read and DNS:Edit on the zone of `BUZZ_DOMAIN`. With `BUZZ_TRAEFIK_DYNAMIC_DIR`, Buzz obtains and renews the certificate for `BUZZ_DOMAIN` and `*.BUZZ_DOMAIN` itself.",
        sensitive=True,
        example="your-cloudflare-api-token",
    ),
    EnvironmentVariable(
        "BUZZ_ACME_DIRECTORY_URL",
        "ACME directory Buzz uses for its wildcard certificate. Point it at the Let's Encrypt staging directory to rehearse issuance.",
        default="https://acme-v02.api.letsencrypt.org/directory",
        example="https://acme-v02.api.letsencrypt.org/directory",
    ),
    EnvironmentVariable(
        "BUZZ_CUSTOM_DOMAINS_ENABLED",
        "Whether this Buzz server enables the optional custom-domain control plane. Requires `BUZZ_TRAEFIK_DYNAMIC_DIR`.",
        default=False,
        example="true",
        parser=parse_bool,
    ),
    EnvironmentVariable(
        "BUZZ_TRAEFIK_HTTPS_ENTRYPOINT",
        "Traefik HTTPS entrypoint expected by custom-domain routers.",
        default="https",
        example="https",
    ),
    EnvironmentVariable(
        "BUZZ_TRAEFIK_SERVICE",
        "Provider-qualified Traefik service used by custom-domain routers.",
        default="buzz@docker",
        example="buzz@docker",
    ),
    EnvironmentVariable(
        "BUZZ_CUSTOM_DOMAIN_OPERATOR_TOKEN",
        "Bearer tokens, separated by commas, for the private custom-domain operator endpoint. The endpoint listens only when this is set.",
        sensitive=True,
        example="replace-with-a-random-secret",
    ),
    EnvironmentVariable(
        "BUZZ_MAX_CUSTOM_DOMAINS_PER_SITE",
        "Maximum pending and verified custom domains for one site.",
        default=5,
        example="5",
        parser=parse_positive_int,
    ),
    EnvironmentVariable(
        "BUZZ_MAX_CUSTOM_DOMAINS_PER_USER",
        "Maximum pending and verified custom domains owned by one user.",
        default=20,
        example="20",
        parser=parse_positive_int,
    ),
    EnvironmentVariable(
        "BUZZ_MAX_CUSTOM_DOMAINS_SERVER_WIDE",
        "Maximum pending and verified custom domains across this Buzz server.",
        default=1000,
        example="1000",
        parser=parse_positive_int,
    ),
    EnvironmentVariable(
        "BUZZ_CUSTOM_DOMAIN_INGRESS_IPS",
        "Comma-separated public ingress IP addresses allowed for direct custom domains.",
        default=frozenset(),
        example="93.184.216.34",
        parser=parse_public_ips,
    ),
    EnvironmentVariable(
        "BUZZ_CUSTOM_DOMAIN_ORIGIN_HOST",
        "Internal Traefik hostname Buzz probes on port 443 to check the wildcard certificate and custom-domain routing.",
        default="coolify-proxy",
        example="coolify-proxy",
    ),
    EnvironmentVariable(
        "BUZZ_TRAEFIK_CERT_RESOLVER",
        "Traefik certificate resolver selected by custom-domain routers. It must issue certificates through HTTP-01.",
        default="letsencrypt",
        example="letsencrypt",
    ),
    EnvironmentVariable(
        "BUZZ_CUSTOM_DOMAIN_RECONCILE_SECONDS",
        "Seconds between custom-domain router reconciliation attempts.",
        default=5,
        example="5",
        parser=parse_positive_int,
    ),
)

ENVIRONMENT_BY_NAME = {variable.name: variable for variable in ENVIRONMENT_VARIABLES}


def environment_value(name: str) -> Any:
    return ENVIRONMENT_BY_NAME[name].read()
