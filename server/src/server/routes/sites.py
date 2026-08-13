from collections.abc import Callable
from typing import Annotated, BinaryIO

from fastapi import APIRouter, Depends, Header, Request, Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from ..access import AccessService
from ..analytics import AnalyticsStore
from ..api_models import (
    DeploySiteResponse,
    ErrorResponse,
    SiteDeploymentResponse,
    SiteResponse,
)
from ..db import Database
from ..serving_content_roots import ServingContentRoots
from ..dependencies import (
    Identity,
    get_database,
    get_settings,
    require_deploy_identity,
    require_user,
)
from ..exceptions import BadRequest, Forbidden, PayloadTooLarge
from ..settings import Settings
from ..site_path import InvalidSubdomain, validated_subdomain
from ..site_store import DeploymentLimits, SiteRecord, SiteStore
from ..utils import generate_subdomain

router = APIRouter()


def validate_site_name(site_name: str) -> str:
    try:
        return validated_subdomain(site_name)
    except InvalidSubdomain:
        raise BadRequest("Invalid site name")


def build_site_url(site_name: str, domain: str | None, fallback_port: int) -> str:
    if domain:
        return f"https://{site_name}.{domain}"
    return f"http://{site_name}.localhost:{fallback_port}"


def deployment_actor(identity: Identity) -> str:
    if identity.token_type != "deploy":
        return identity.user.github_login
    name = "".join(
        char for char in (identity.token_name or "") if char.isprintable()
    ).strip()
    return name[:100] or "Deployment token"


def _deployment_limits(settings: Settings) -> DeploymentLimits:
    return DeploymentLimits(
        max_archive_bytes=settings.max_archive_bytes,
        max_site_bytes=settings.max_site_bytes,
        max_entries=settings.max_site_files,
        max_path_bytes=settings.max_archive_path_bytes,
    )


def _deploy_site(
    database: Database,
    settings: Settings,
    site_name: str,
    archive: BinaryIO,
    owner_id: int,
    source: str,
    actor: str,
    credential: str | None,
    configure: Callable | None,
    content_roots: ServingContentRoots,
) -> SiteRecord:
    with database.connect() as conn:
        store = SiteStore(
            conn,
            settings.sites_dir,
            _deployment_limits(settings),
            settings.deployments_dir,
            content_roots,
        )
        return store.deploy(
            site_name,
            archive,
            owner_id,
            configure,
            source=source,
            actor=actor,
            credential=credential,
        )


def _delete_site(
    database: Database,
    settings: Settings,
    name: str,
    owner_id: int,
    content_roots: ServingContentRoots,
) -> None:
    with database.connect() as conn:
        SiteStore(
            conn,
            settings.sites_dir,
            deployments_dir=settings.deployments_dir,
            content_roots=content_roots,
        ).delete(name, owner_id)


def _activate_site_deployment(
    database: Database,
    settings: Settings,
    name: str,
    deployment_number: int,
    owner_id: int,
    content_roots: ServingContentRoots,
):
    with database.connect() as conn:
        return SiteStore(
            conn,
            settings.sites_dir,
            deployments_dir=settings.deployments_dir,
            content_roots=content_roots,
        ).activate_deployment(name, deployment_number, owner_id)


@router.post(
    "/deploy",
    response_model=DeploySiteResponse,
    operation_id="deploySite",
    summary="Deploy a site",
    description=(
        "Upload a ZIP archive as a new deployment. It becomes active immediately. "
        "Buzz creates the site if needed. A deployment token may "
        "deploy only to its assigned site and must send that name in X-Buzz-Site."
    ),
    responses={
        400: {
            "model": ErrorResponse,
            "description": "The site name or archive is invalid.",
        },
        401: {"model": ErrorResponse, "description": "Authentication is required."},
        403: {
            "model": ErrorResponse,
            "description": "The credential cannot deploy this site.",
        },
        413: {
            "model": ErrorResponse,
            "description": "A deployment limit was exceeded.",
        },
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {
                            "file": {
                                "type": "string",
                                "format": "binary",
                                "description": "A ZIP archive containing the site's files.",
                            }
                        },
                    }
                }
            },
        }
    },
)
async def deploy(
    request: Request,
    database: Annotated[Database, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_settings)],
    identity: Identity = Depends(require_deploy_identity),
    x_buzz_site: str | None = Header(
        default=None,
        description="Site to deploy to. Buzz generates a name when omitted.",
    ),
    x_subdomain: str | None = Header(default=None, include_in_schema=False),
    x_buzz_access: str | None = Header(
        default=None,
        description="Set to 'private' to publish the site and protect it atomically.",
    ),
):
    # Rejected rather than ignored: an unrecognised name header would otherwise
    # fall through to generate_subdomain() and silently publish to a new random
    # site, reporting success while the intended site went untouched.
    if x_subdomain is not None:
        raise BadRequest(
            "X-Subdomain was replaced by X-Buzz-Site. Upgrade the Buzz CLI "
            "(npm i -g @infomiho/buzz-cli) or send X-Buzz-Site instead."
        )
    site_name = validate_site_name(x_buzz_site) if x_buzz_site else generate_subdomain()
    if not identity.can_deploy_to(site_name):
        raise Forbidden(
            f"Deploy token is scoped to site '{identity.site_name}', cannot deploy to '{site_name}'"
        )
    make_private = False
    if x_buzz_access is not None:
        if identity.token_type != "session":
            raise Forbidden("Deployment tokens cannot manage access")
        if x_buzz_access != "private":
            raise BadRequest("X-Buzz-Access must be 'private'")
        make_private = True
    source = "api" if request.headers.get("authorization") else "dashboard"
    actor = identity.user.github_login
    credential = deployment_actor(identity) if identity.token_type == "deploy" else None

    async with request.form(max_files=1, max_fields=1) as form:
        file = form.get("file")
        if not isinstance(file, UploadFile):
            raise BadRequest("Missing ZIP file")
        if file.size is not None and file.size > settings.max_archive_bytes:
            raise PayloadTooLarge(
                f"ZIP exceeds the {settings.max_archive_bytes}-byte compressed upload limit"
            )

        await file.seek(0)
        configure = (
            request.app.state.access.begin_private_publication(
                site_name, identity.user.id
            )
            if make_private
            else None
        )
        record = await run_in_threadpool(
            _deploy_site,
            database,
            settings,
            site_name,
            file.file,
            identity.user.id,
            source,
            actor,
            credential,
            configure,
            request.app.state.content_roots,
        )

    # Report the site's actual visibility, not the flag that was passed: a
    # redeploy of an already-private site must still say so.
    with database.connect() as conn:
        private = bool(AccessService.private_site_names_on_connection(conn, [record.name]))

    return {
        "name": record.name,
        "site_name": record.name,
        "url": build_site_url(record.name, settings.domain, request.url.port or 8080),
        "private": private,
        "deployment_number": record.deployment_number,
    }


@router.get(
    "/sites/{name}/deployments",
    response_model=list[SiteDeploymentResponse],
    operation_id="listSiteDeployments",
    summary="List site deployments",
)
async def list_site_deployments(
    name: str,
    identity: Annotated[Identity, Depends(require_user)],
    database: Annotated[Database, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    with database.connect() as conn:
        deployments = SiteStore(
            conn, settings.sites_dir, deployments_dir=settings.deployments_dir
        ).list_deployments(name, identity.user.id)
    return [deployment.__dict__ for deployment in deployments]


@router.post(
    "/sites/{name}/deployments/{deployment_number}/activate",
    response_model=SiteDeploymentResponse,
    operation_id="activateSiteDeployment",
    summary="Activate a site deployment",
)
async def activate_site_deployment(
    request: Request,
    name: str,
    deployment_number: int,
    identity: Annotated[Identity, Depends(require_user)],
    database: Annotated[Database, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    deployment = await run_in_threadpool(
        _activate_site_deployment,
        database,
        settings,
        name,
        deployment_number,
        identity.user.id,
        request.app.state.content_roots,
    )
    return deployment.__dict__


@router.get(
    "/sites",
    response_model=list[SiteResponse],
    operation_id="listSites",
    summary="List owned sites",
    responses={
        401: {"model": ErrorResponse, "description": "Authentication is required."},
        403: {"model": ErrorResponse, "description": "A session token is required."},
    },
)
async def list_sites(
    identity: Annotated[Identity, Depends(require_user)],
    database: Annotated[Database, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    with database.connect() as conn:
        store = SiteStore(conn, settings.sites_dir)
        sites = store.list_for_owner(identity.user.id)
        names = [site.name for site in sites]
        views_by_site = AnalyticsStore(conn).total_views_by_site(names)
        private_names = AccessService.private_site_names_on_connection(conn, names)
    return [
        {
            "name": site.name,
            "created": site.last_deployed_at,
            "last_deployed_at": site.last_deployed_at,
            "size_bytes": site.size_bytes,
            "total_views": views_by_site[site.name],
            "private": site.name in private_names,
        }
        for site in sites
    ]


@router.delete(
    "/sites/{name}",
    status_code=204,
    operation_id="deleteSite",
    summary="Delete a site",
    responses={
        401: {"model": ErrorResponse, "description": "Authentication is required."},
        403: {
            "model": ErrorResponse,
            "description": "A session token and site ownership are required.",
        },
        404: {"model": ErrorResponse, "description": "The site does not exist."},
        409: {
            "model": ErrorResponse,
            "description": "Every custom domain must complete removal before deleting the site.",
        },
    },
)
async def delete_site(
    request: Request,
    name: str,
    identity: Annotated[Identity, Depends(require_user)],
    database: Annotated[Database, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    await run_in_threadpool(
        _delete_site,
        database,
        settings,
        name,
        identity.user.id,
        request.app.state.content_roots,
    )
    return Response(status_code=204)
