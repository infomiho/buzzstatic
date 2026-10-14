# Buzz

Self-hosted static site hosting with CLI deployment.

## Project Structure

- `server/` - Python 3.12+ FastAPI server, dependencies managed with uv. Jinja2 templates (`src/server/templates/`) use the Achroma visual system; `src/server/static/` holds built CSS/JS assets (Tailwind, built via the server's `package.json`). Custom-domain logic lives in the `src/server/custom_domains/` internal package: `CustomDomainsRuntime` owns wiring, the reconcile loop, startup guards, and capabilities; `ClaimView` is the single claim read interface; the package ships its own schema (`schema.py`) and errors. The host imports only names re-exported from the package root; the claim state machine and other collaborators are internal.
- `cli/` - TypeScript CLI (Commander.js + Archiver), published to npm as `@infomiho/buzz-cli`.
- `docs/site/` - Starlight docs site. `reference/configuration.md`, `server/.env.example`, and `public/openapi.json` are generated from `server/src/server/environment.py`, the single source of truth for environment variables, via `npm run generate:server` in `docs/site`. `reference/cli/index.md` is generated from the Commander definitions via `npm run generate:cli`. The Docs CI job fails if either is stale.
- `docs/agents/` - instructions for agent skills.

Access is the protection layer for hosted sites. A site is either public or private; a private site is readable by its owner and explicitly added GitHub readers, across every path and asset. Reader membership belongs to the current private policy, so making a site public clears its readers. Authentication is separate from control-plane admission: a reader can sign in for a shared site without gaining dashboard, deployment, domain, token, or analytics access. Policies persist independently of deployments. `AccessService.check_request` takes no request path by design: the static resolver serves one file under several URLs (`<path>.html`, `<path>/index.html`, the `200.html` catch-all), so any path-derived decision can disagree with the file served. The control-host Buzz session authorizes a short-lived, single-use handoff to a separate host-only Access grant; dashboard cookies are never shared with hosted sites. Reader authorization is rechecked when issuing and exchanging the handoff and on every hosted request, so removal is immediate. `buzz access` reports and changes visibility; `buzz deploy --private` publishes and protects atomically. Deployment tokens cannot change Access. Serving consults an in-process visibility cache so public sites skip the database entirely; sites that may be protected get the full check on a long-lived reader connection off the event loop. The cache assumes a single process with every visibility write funnelled through the one `AccessService` instance.

## Server

Run locally:
```bash
cd server
uv sync
npm install && npm run css:build
uv run python -m server --dev
```

`npm run css:watch` rebuilds CSS on change.

Test:
```bash
cd server
uv run pytest tests/ -v
```

## CLI

Build and test:
```bash
cd cli
npm install
npm run build
npm test
```

`npm link` installs it globally for development.

Commands: `deploy`, `list`, `delete`, `url`, `config`, `login`, `logout`, `whoami`, `tokens`, `domains`, `access`. Config lives at `~/.buzz.config.json`; the per-project site name in a `CNAME` file. Custom domains do not change the canonical deployment identity or local `CNAME`.

## Deployment

Two deployment paths, both running the published image (`ghcr.io/infomiho/buzzstatic`) behind Traefik v3: a standalone Docker Compose stack (`server/docker-compose.yml`) and Coolify. Required settings on both: `BUZZ_DOMAIN`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `BUZZ_CLOUDFLARE_DNS_API_TOKEN`. An empty value counts as unset. The image runs from its baked venv without `uv`, and compose healthchecks call `/app/.venv/bin/python`. A 0.6 Compose file with only its image tag bumped leaves the container unhealthy, because its healthcheck calls `uv`, so upgrades replace the whole file.

The standalone compose is replaced on every update and never edited: it reads the whole `.env` through `env_file`, keeps Traefik's static config in constant `TRAEFIK_*` env keys (never a `command:`, which makes Traefik ignore them), and expects local changes in `compose.override.yml`. Traefik before v3.6.1 cannot read the Docker provider on Docker Engine 29.

Coolify production uses the root `docker-compose.coolify.yml`, which builds from source. Pushes to `main` auto-deploy, so production tracks `main` deliberately. Other Coolify operators use `server/docker-compose.coolify.yml`, which pins the published image and is bumped by every server release. Both: enable Raw Docker Compose Deployment, leave the app FQDN empty, and bind-mount `/data/coolify/proxy/dynamic` at `/traefik-dynamic`. Environment changes take effect on restart or redeployment. Coolify compose files never use `${VAR:?msg}`, because Coolify stores the message as the variable's value. Buzz needs no proxy edits, so **Servers > Proxy > Reset configuration to default** is always safe. Any proxy change made for other reasons must be saved through **Servers > Proxy**, because Coolify rewrites `/data/coolify/proxy/docker-compose.yml` from its DB.

Buzz is the only source of the wildcard certificate: the `wildcard_certificate` package orders `BUZZ_DOMAIN` and `*.BUZZ_DOMAIN` over Cloudflare DNS-01, keeps it in `/data/tls`, and publishes it to `BUZZ_TRAEFIK_DYNAMIC_DIR/buzz-certificate.yaml`. Never add a proxy-issued wildcard for the same names, because Traefik then picks a certificate at random after restarts. Buzz labels set `tls=true` without `tls.certresolver`. Every write to the dynamic directory goes through `traefik_file_provider.TraefikDynamicFile`, because one malformed file stalls the whole file provider. Never write `tls.stores`, which would hijack the proxy-wide default certificate. File, router, and volume names are fixed, so one proxy serves one Buzz instance. `/health` reports `degraded`, still with HTTP 200, when the certificate the proxy serves is invalid or expiring or Buzz cannot obtain or publish it.

Custom domains are an optional operator capability and default to disabled through the single switch `BUZZ_CUSTOM_DOMAINS_ENABLED`. Once enabled with `BUZZ_TRAEFIK_DYNAMIC_DIR` and `BUZZ_CUSTOM_DOMAIN_INGRESS_IPS` configured, admission, routing, and Cloudflare support are all derived from configuration presence plus runtime health, not from separate toggles. Disabled or unhealthy custom-domain infrastructure must not affect canonical Buzz hosting. Buzz writes its routers to `buzz-routes.yaml` in the dynamic directory. Publication and withdrawal are acknowledged only when a probe through the proxy shows the revision router on `buzz-config-revision.invalid` serving the revision Buzz last wrote. Disabling with routers still published withdraws them before claims become `not_routed`. When routers exist but `BUZZ_TRAEFIK_DYNAMIC_DIR` is unset, Buzz still serves canonical sites, answers custom hostnames with 421, and reports `custom_domains_error: routers_unmanaged` in `/health`.

Onboarding is always automatic: a site owner adds a hostname and Buzz detects whether DNS points direct or through Cloudflare and onboards accordingly, switching automatically if the DNS later changes. There is no manual direct/Cloudflare selection. Cloudflare support is available whenever the bundled IP range snapshot is valid and the reconcile runtime is up. When a Cloudflare-pointed domain cannot be validated, the owner is told to point DNS directly instead. Persistent ownership, Cloudflare ranges, edge challenge, and origin identity failures fail closed immediately. All addresses in runtime-reachable families must validate. A wholly unroutable IPv6 family is tolerated only when IPv4 fully validates. DNS range checks and confirmation still cover every A and AAAA answer. Edge and origin transport failures receive three attempts. The bundled range snapshot fails closed after 180 days.

Cloudflare-to-direct handoffs can temporarily lose public reachability when Cloudflare stops serving before cached Cloudflare DNS answers expire. Source health remains fail-closed. If it clears activation, the coordinator must preserve the intended target and continue generation-fenced unactivated validation rather than restart onboarding from a stale source observation.

## Releasing

Squash-merge PRs: a merge commit carries the PR title in its body, so Release Please counts it as a second conventional commit and attributes the whole PR diff to every package it touches (duplicate changelog entries, spurious releases).

Release Please versions two packages independently: the CLI (`@infomiho/buzz-cli` npm package, tags `buzz-cli-vX.Y.Z`) and the server (tags `server-vX.Y.Z`, version in `server/pyproject.toml` and `server/src/server/__init__.py`). Commits are routed by path, so `cli/` and `server/` changes release separately. Use conventional commits on `main`: `fix:` patch, `feat:` minor, `feat!:` minor while pre-1.0. `bump-minor-pre-major` keeps breaking changes off 1.0.0; without it Release Please promotes the first `feat!:` straight to 1.0.0. Merging a CLI release PR publishes to npm via OIDC trusted publishing. Merging a server release PR builds a multi-arch (amd64 + arm64, native runners) image and pushes it to `ghcr.io/infomiho/buzzstatic` with `X.Y.Z`, `X.Y`, and `latest` tags (`X` once the server reaches 1.0). Coolify production still builds from source on push to `main`, independent of server releases.

## Agent skills

- Issue tracker: GitHub Issues on `infomiho/buzzstatic` via `gh`, see `docs/agents/issue-tracker.md`.
- No triage labels are used, see `docs/agents/triage-labels.md`.
- No domain docs exist; use this file as project context, see `docs/agents/domain.md`.
