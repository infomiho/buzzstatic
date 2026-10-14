---
title: Troubleshoot Self-Hosting
description: Diagnose startup, DNS, TLS, routing, authentication, and data failures.
sidebar:
  order: 2
---

Start with container status and logs, then test DNS, TLS, and the health endpoint separately. This order distinguishes a server failure from a proxy or DNS failure.

## A Container Doesn't Stay Running

For standalone Docker Compose, inspect status and logs:

```bash
docker compose ps
docker compose logs server traefik
```

For Coolify, open the application's deployment and runtime logs, then check the proxy logs under **Servers > Proxy**.

Common server startup causes include:

- `GitHub OAuth not configured`: Set both `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET`, then restart Buzz.
- `ERROR: Invalid <NAME>`: correct the named variable and restart Buzz.
- `ERROR: BUZZ_DOMAIN ...`: set `BUZZ_DOMAIN` to a bare hostname such as `buzz.example.com`, without a scheme, port, or path.
- `Could not reconcile ... deployment operation(s)`: Buzz found an unreadable or inconsistent journal under `/data/sites/.operations` and stopped to avoid publishing uncertain state.
- `Database schema version ... is newer than supported version`: a newer Buzz already migrated the database. Start that newer release again, or [restore the pre-migration backup](../../self-hosting/manage-data-and-backups/#restore-a-pre-migration-backup) the message names.
- `Could not back up the database ... before migrating`: Buzz refuses to migrate without a backup. Free disk space on the data volume and restart Buzz.

For reconciliation failures, stop Buzz and create a cold copy of the complete data volume before investigating. Do not remove the journal or edit `data.db` independently. Buzz has no automated repair command for an operation it can't reconcile.

## The Health Endpoint Can't Be Reached

Test each layer:

1. Confirm `buzz.example.com` resolves to the public server IP:

   ```bash
   dig +short buzz.example.com
   ```

2. Confirm the server container is running, reports `healthy` in `docker ps`, and is exposed to Traefik on port `8080`. Traefik drops routes to unhealthy containers.
3. Check the Traefik logs for `client version 1.24 is too old`. Traefik releases before v3.6.1 cannot read routes from Docker Engine 29 and later. With Docker Compose, use the Traefik release pinned in the [Compose file](../../self-hosting/docker-compose/) or a newer one. With Coolify, keep the proxy image that Coolify's current release ships.
4. Confirm ports `80` and `443` are allowed by the host firewall and infrastructure firewall.
5. Request the public endpoint:

   ```bash
   curl --verbose https://buzz.example.com/health
   ```

A `421 Misdirected Request` means the request's `Host` header doesn't match `BUZZ_DOMAIN` or one of its site subdomains. Check `BUZZ_DOMAIN`, DNS, and proxy labels, then restart the server after correcting the environment.

## The Dashboard Works But Site Hostnames Don't

1. Check the wildcard record:

   ```bash
   dig +short my-site.buzz.example.com
   ```

2. Confirm it returns the same IP as `buzz.example.com`.
3. Confirm the Traefik configuration includes a wildcard `HostRegexp` route.
4. Confirm the certificate covers `*.buzz.example.com`.

The DNS record for `buzz.example.com` doesn't cover site hostnames. Add a separate `*.buzz` record as described in [Configure DNS And TLS](../../self-hosting/configure-dns-and-tls/).

## TLS Certificate Issuance Fails

Read `wildcard_certificate_error` from `/health`, then find the matching `Wildcard certificate:` line in the Buzz server logs:

- `not_configured`: `BUZZ_CLOUDFLARE_DNS_API_TOKEN` is missing.
- `issuance_failed`: the order failed. The log line names the Cloudflare or ACME error.
- `storage_failed`: Buzz could not save the certificate to `/data/tls`, usually a full disk. It keeps serving the issued certificate and saves it on the next attempt.
- `publish_failed`: Buzz could not write `buzz-certificate.yaml`.

Then check:

- `BUZZ_CLOUDFLARE_DNS_API_TOKEN` has Zone:Read and DNS:Edit for the zone of `BUZZ_DOMAIN`.
- The authoritative DNS zone is hosted by Cloudflare.
- Only one Buzz instance writes to this proxy's dynamic directory.
- The server can reach `api.cloudflare.com` and the ACME directory over HTTPS, and the zone's nameservers over DNS.
- `BUZZ_TRAEFIK_DYNAMIC_DIR` points at a directory Traefik's file provider watches, and Buzz can write to it.
- No other file in that directory is malformed. One broken file stops Traefik from loading the whole directory.
- No other wildcard certificate for the same names exists in Traefik's `acme.json` or another dynamic file. Traefik picks either one at random after a restart.
- Buzz's routers use `tls=true` without `tls.certresolver`.
- `BUZZ_ACME_DIRECTORY_URL` is unset, unless you are rehearsing against staging.

## Cloudflare Proxy Diagnostics

Cloudflare claims return `421` until every required check passes. Active domains show degraded health during the first two consecutive edge or origin transport failures and stop serving on the third. Ownership, non-Cloudflare DNS, and challenge identity failures stop serving immediately. A fully healthy pass recovers automatically.

Standard Coolify Docker networks are IPv4-only unless IPv6 is explicitly enabled. Cloudflare can return A records and synthetic AAAA records for a proxied hostname. Buzz still validates every answer against Cloudflare ranges and includes every answer in DNS confirmation, but it does not require successful IPv6 connections when every IPv6 address reports that the container runtime cannot route IPv6 and every IPv4 address fully validates. Any partial IPv6 failure, ordinary transport error, TLS error, or challenge error remains actionable and blocks activation.

- `ownership_txt_mismatch`: restore the exact TXT ownership value. Buzz has stopped serving the hostname.
- `ownership_dns_unavailable`: the ownership lookup failed. Buzz has stopped serving because current ownership cannot be established.

- `edge_tls_invalid`: wait for Universal SSL or correct the hostname's edge certificate.
- `cloudflare_525`: Cloudflare could not complete the TLS handshake with the origin.
- `cloudflare_526`: Full (strict) rejected the origin certificate.
- `cloudflare_1014`: the hostname uses a cross-account Cloudflare CNAME that requires Cloudflare for SaaS or a different DNS arrangement.
- `edge_redirect`: remove redirect rules from the Buzz verification path.
- `edge_cached_challenge`: bypass cache for the current generation's verification path.
- `edge_waf_denied` or `edge_challenge_present`: bypass WAF, bot, Access, or managed challenge rules.
- `http_forward_blocked` or `http_forward_cached_challenge`: remove port-80 forwarding interference. `http_forward_redirect` records the observed redirect but does not prove that Traefik's intercepted ACME path fails.
- `dns_mixed_cloudflare_addresses`: every A and AAAA answer must be Cloudflare-owned for a Cloudflare hostname.
- `range_data_stale`, `range_data_invalid`, or `range_data_missing`: upgrade Buzz to refresh the bundled Cloudflare range snapshot. Buzz doesn't dial public addresses until it is valid. Buzz warns in its logs, on the site dashboard, and in `GET /capabilities/custom-domains` during the 30 days before the snapshot goes stale. Upgrading to the latest release refreshes it.

Buzz does not change DNS, follow redirects, or request customer Cloudflare credentials.

Let's Encrypt issuance can also fail because of external availability or certificate rate limits. Do not repeatedly restart Buzz or Traefik while the same ACME error persists.

An activated custom hostname can answer `421` for up to about 90 seconds after its router or certificate changes, for example after re-enabling custom domains or resetting the proxy, while Buzz checks it again.

If every custom hostname answers `421` and `/health` reports `"custom_domains_error": "routers_unmanaged"`, Buzz has custom-domain routers but `BUZZ_TRAEFIK_DYNAMIC_DIR` is unset, usually because it was removed from the Compose file or the environment. Buzz's own sites keep working. The `Custom domains are offline:` log line names the cause. Use the current Compose file, or set `BUZZ_TRAEFIK_DYNAMIC_DIR` and its bind mount again, as in [Disable Custom Domains](../../self-hosting/coolify/#disable-custom-domains).

## GitHub Sign-In Doesn't Start Or Complete

1. Confirm the OAuth app's callback URL is `https://buzz.example.com/dashboard/login/github/callback`, replacing the hostname with your Buzz domain.
2. Confirm `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET` belong to that OAuth app and are present in the running container, then restart Buzz.
3. Confirm the server can reach `github.com` and `api.github.com` over HTTPS.
4. Start a new sign-in if Buzz says the request expired or was cancelled.

## Data Disappears After A Redeploy

Confirm `/data` is mounted from the named volume `buzz_buzz-data`. A container filesystem without this mount is ephemeral.

```bash
docker volume inspect buzz_buzz-data
```

Do not run `docker compose down --volumes`, `docker system prune --volumes`, or `docker volume prune` during routine maintenance. In Coolify, also check whether optional unused-volume cleanup ran while Buzz was stopped. If the volume was removed, restore the latest checksum-verified backup using [Manage Data And Backups](../../self-hosting/manage-data-and-backups/). Buzz can't reconstruct `data.db` ownership, sessions, tokens, and analytics from site files alone.
