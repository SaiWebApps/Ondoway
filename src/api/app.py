"""FastAPI application factory for the Ondoway Graph API."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from neo4j.exceptions import ServiceUnavailable

from src.api.auth.routes import router as auth_router
from src.api.dependencies import close_driver, get_resume_coordinator, init_driver
from src.api.routes import (
    audio,
    edges,
    families,
    feedback,
    graph,
    nodes,
    onboard,
    product,
    schema,
    trips,
)


def _workbench_api_enabled() -> bool:
    """Whether to mount the editorial-workbench graph-CRUD routers (graph, nodes,
    edges, schema) + the new-city onboard router.

    FAIL-CLOSED / DEFAULTS OFF (opt-in): mounts ONLY when WORKBENCH_API_ENABLED is
    an EXPLICIT truthy value (true/1/yes/on, case-insensitive, whitespace-trimmed).
    An unset, empty, or typo'd/garbage value ("disbaled", "flase", "") keeps the
    surface OFF. This is a security property: these routers are an UNAUTHENTICATED
    create/update/DETACH-DELETE + data/-write-and-deploy surface, so under the old
    fail-OPEN default a single mis-set env var silently exposed it over the
    internet. Local dev + the test suite opt in explicitly (Makefile `api`/
    `api-test`, scripts/workbench.sh, tests/conftest.py); prod render.yaml pins
    "false" (still OFF under this parser)."""
    return os.getenv("WORKBENCH_API_ENABLED", "").strip().lower() in (
        "true",
        "1",
        "yes",
        "on",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown: manage Neo4j driver lifecycle."""
    init_driver()
    yield
    close_driver()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Ondoway Graph API",
        version="0.1.0",
        description="CRUD API for the Ondoway Neo4j graph database",
        lifespan=lifespan,
    )

    origins = os.getenv("CORS_ORIGINS", "*").split(",")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ServiceUnavailable)
    async def _neo4j_unavailable_handler(request: Request, exc: ServiceUnavailable):
        """A DB call hit an unreachable Neo4j (paused Aura, outage). Nudge the
        instance awake (async) and return a fast, clean 503 with Retry-After
        instead of a raw 500. NO-OP nudge when Aura resume is disabled."""
        aura_status = "disabled"
        coordinator = get_resume_coordinator()
        if coordinator is not None:
            aura_status = coordinator.ensure_resuming()
        return JSONResponse(
            status_code=503,
            content={"detail": "Database is waking up, retry shortly", "aura": aura_status},
            headers={"Retry-After": "30"},
        )

    _frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
    _auth_html = _frontend_dir / "auth.html"
    _join_family_html = _frontend_dir / "join-family.html"

    @app.get("/auth")
    async def auth_redirect():
        if not _auth_html.is_file():
            from fastapi import HTTPException

            raise HTTPException(404, "auth redirect page not found")
        return FileResponse(str(_auth_html), media_type="text/html")

    @app.get("/auth/join-family")
    async def join_family_redirect():
        """The invite URL's browser landing (auth.html's mould): deep-link the
        token into the app's join route, with a get-the-app line for a phone
        without the app. The same path is also an Apple universal link
        (/auth/* below), so a phone WITH the app never sees this page."""
        if not _join_family_html.is_file():
            from fastapi import HTTPException

            raise HTTPException(404, "join-family redirect page not found")
        return FileResponse(str(_join_family_html), media_type="text/html")

    @app.get("/api/v1/healthz")
    async def healthz():
        """Report which Neo4j the API is connected to (plus a liveness probe).

        Test fixtures use this to verify they are talking to the intended
        instance (the workbench suite requires its dedicated 7689 to be live)
        before seeding data, so an API on another graph (``make api`` → 7687
        dev, or the shared pytest DB on 7688) that happens to be listening on
        the same HTTP port can never be reused and seeded with test rows.
        ``neo4j_port`` is parsed from the configured ``NEO4J_URI`` (what the
        driver connected to); ``neo4j_connected`` runs a trivial query to
        confirm the driver is actually live.
        """
        from urllib.parse import urlparse

        from src.api.dependencies import get_driver
        from src.connection import get_database

        uri = os.getenv("NEO4J_URI", "")
        database = get_database()
        connected = False
        try:
            driver = get_driver()
            with driver.session(database=database) as session:
                session.run("RETURN 1 AS ok").single()
            connected = True
        except Exception:
            connected = False

        body = {
            "status": "ok" if connected else "degraded",
            "neo4j_uri": uri,
            "neo4j_port": urlparse(uri).port,
            "neo4j_database": database,
            "neo4j_connected": connected,
        }
        # When the probe fails AND Aura resume is enabled, nudge the (possibly
        # paused) instance awake and surface its status. Disabled/connected =>
        # body is unchanged (existing test_api_startup contract).
        if not connected:
            coordinator = get_resume_coordinator()
            if coordinator is not None:
                body["aura"] = coordinator.ensure_resuming()
        return JSONResponse(content=body)

    @app.get("/.well-known/apple-app-site-association")
    async def apple_app_site_association():
        from src.api.auth.config import APPLE_TEAM_ID, BUNDLE_ID

        if not APPLE_TEAM_ID:
            from fastapi import HTTPException

            raise HTTPException(500, "APPLE_TEAM_ID not configured")

        return JSONResponse(
            content={
                "applinks": {
                    "apps": [],
                    "details": [
                        {
                            "appID": f"{APPLE_TEAM_ID}.{BUNDLE_ID}",
                            "paths": ["/auth", "/auth/*"],
                        }
                    ],
                }
            },
            media_type="application/json",
        )

    app.include_router(auth_router, prefix="/api/v1")
    # Mobile-facing routes: always mounted (the app and the editorial workbench both
    # call these; audio/trips carry their own compose/VERIFY gates).
    app.include_router(audio.router, prefix="/api/v1")
    app.include_router(trips.router, prefix="/api/v1")
    app.include_router(families.router, prefix="/api/v1")
    app.include_router(feedback.router, prefix="/api/v1")
    # Public read-only product surface (lens taxonomy, user profile): the
    # mobile client's read path. Mounted here — outside the
    # _workbench_api_enabled() gate below — because it must serve on the
    # public deployment where that flag is "false".
    app.include_router(product.router, prefix="/api/v1")
    # Editorial-workbench CRUD (create/update/delete graph nodes + edges, read the
    # schema/city catalogue). These are the LOCAL workbench's surface — an
    # UNAUTHENTICATED write surface — so they must NOT be exposed on the public
    # deployment. FAIL-CLOSED: mounted ONLY on an explicit truthy
    # WORKBENCH_API_ENABLED (opt-in). Local dev + the test/CI suites set it
    # explicitly (Makefile, scripts/workbench.sh, conftest); the public Render
    # service leaves it "false" so anonymous callers cannot mutate (or crash) the
    # graph over the internet — and, crucially, a typo'd/unset value now also
    # stays OFF instead of silently exposing the surface.
    if _workbench_api_enabled():
        app.include_router(graph.router, prefix="/api/v1")
        app.include_router(nodes.router, prefix="/api/v1")
        app.include_router(edges.router, prefix="/api/v1")
        app.include_router(schema.router, prefix="/api/v1")
        # New-city onboarding: writes data/ + shells out to scripts.deploy, so it
        # MUST stay behind the same gate as the workbench CRUD surface (prod
        # WORKBENCH_API_ENABLED=false => never mounted).
        app.include_router(onboard.router, prefix="/api/v1")

    # Serve the graph editor frontend
    editor_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "frontend",
        "editor",
    )
    if os.path.isdir(editor_dir):
        app.mount("/editor", StaticFiles(directory=editor_dir, html=True), name="editor")

    return app


app = create_app()
