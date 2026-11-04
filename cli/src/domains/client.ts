import { domainToASCII } from "node:url";
import {
  CliError,
  isSiteArray,
  requestEmpty,
  requestJson,
  type ApiErrors,
  type Target,
} from "../client.js";
import { sessionRequired } from "../target.js";
import {
  isCapability,
  isClaim,
  isClaimArray,
  normalizeCapability,
  normalizeClaim,
  normalizeClaimArray,
  type DomainCapability,
  type DomainClaim,
} from "./schema.js";

const domainErrors: ApiErrors = {
  forbidden: (message, target) =>
    message === "Deploy tokens cannot perform this operation"
      ? sessionRequired(target, "Deployment tokens cannot manage custom domains")
      : new CliError(message),
};

const invalidClaim = "Server returned an invalid custom-domain response";

function claimSpec() {
  return { guard: isClaim, invalid: invalidClaim, normalize: normalizeClaim };
}

function domainPath(siteName: string, suffix = ""): string {
  return `/sites/${encodeURIComponent(siteName)}/domains${suffix}`;
}

export async function getDomainCapability(
  target: Target
): Promise<DomainCapability> {
  return requestJson(
    "/capabilities/custom-domains",
    {
      guard: isCapability,
      invalid: "Server returned an invalid custom-domain capability response",
      normalize: normalizeCapability,
    },
    {},
    {
      target,
      errors: {
        ...domainErrors,
        notFound: new CliError(
          "This Buzz server does not expose custom-domain capability information",
          "Update the server before using CLI domain management"
        ),
        fallback: "Could not check custom-domain capability",
      },
    }
  );
}

export async function getDomainClaims(
  siteName: string,
  target: Target
): Promise<DomainClaim[]> {
  return requestJson(
    domainPath(siteName),
    { guard: isClaimArray, invalid: invalidClaim, normalize: normalizeClaimArray },
    {},
    {
      target,
      errors: {
        ...domainErrors,
        notFound: `Site '${siteName}' not found`,
        fallback: "Could not list custom domains",
      },
    }
  );
}

export async function getAllDomainClaims(
  target: Target
): Promise<DomainClaim[]> {
  const sites = await requestJson(
    "/sites",
    { guard: isSiteArray, invalid: "Server returned an invalid site response" },
    {},
    { target, errors: { ...domainErrors, fallback: "Could not list sites" } }
  );
  const claims: DomainClaim[] = [];
  for (const site of sites) {
    claims.push(...(await getDomainClaims(site.name, target)));
  }
  return claims.sort((a, b) =>
    `${a.site_name ?? ""}\0${a.hostname}`.localeCompare(
      `${b.site_name ?? ""}\0${b.hostname}`
    )
  );
}

export function resolveDomainClaim(
  claims: DomainClaim[],
  rawHostname: string
): DomainClaim {
  const input = rawHostname.trim();
  const hostname = domainToASCII(input).toLowerCase().replace(/\.$/, "");
  const matches = claims.filter((claim) => claim.hostname === hostname);
  const active = matches.filter((claim) =>
    ["pending", "verified"].includes(claim.status)
  );
  const claim = [...(active.length ? active : matches)].sort((a, b) => b.id - a.id)[0];
  if (!claim) throw new CliError(`Custom domain '${rawHostname}' not found`);
  return claim;
}

export async function createDomainClaim(
  siteName: string,
  hostname: string,
  target: Target
): Promise<DomainClaim> {
  return requestJson(
    domainPath(siteName),
    claimSpec(),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hostname }),
    },
    { target, errors: { ...domainErrors, fallback: "Could not add custom domain" } }
  );
}

export async function checkDomainClaim(
  siteName: string,
  claimId: number,
  target: Target
): Promise<DomainClaim> {
  return requestJson(
    domainPath(siteName, `/${claimId}/check`),
    claimSpec(),
    { method: "POST" },
    { target, errors: { ...domainErrors, fallback: "Could not check custom domain" } }
  );
}

export async function cancelDomainClaim(
  siteName: string,
  claimId: number,
  target: Target
): Promise<202 | 204> {
  return requestEmpty(
    domainPath(siteName, `/${claimId}`),
    [202, 204] as const,
    { method: "DELETE" },
    { target, errors: { ...domainErrors, fallback: "Could not remove custom domain" } }
  );
}

async function updateDomainTransition(
  siteName: string,
  claimId: number,
  action: "retry" | "cancel",
  target: Target
): Promise<DomainClaim> {
  return requestJson(
    domainPath(siteName, `/${claimId}/transition/${action}`),
    claimSpec(),
    { method: "POST" },
    {
      target,
      errors: {
        ...domainErrors,
        fallback: `Could not ${action} custom-domain transition`,
      },
    }
  );
}

export const retryDomainTransition = (
  siteName: string,
  claimId: number,
  target: Target
) => updateDomainTransition(siteName, claimId, "retry", target);

export const cancelDomainTransition = (
  siteName: string,
  claimId: number,
  target: Target
) => updateDomainTransition(siteName, claimId, "cancel", target);
