"""FastAPI app. Routers and lifecycle modules are optional so a partially built app still boots.

Router module contract: exposes `router: fastapi.APIRouter` (paths start with /api).
Lifecycle module contract (all optional): `mount(app)` called at creation (e.g. MCP sub-app at /mcp),
and `lifespan(app)` — an async context manager entered at startup in listed order.
"""
from __future__ import annotations

import importlib
import logging
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config, db

log = logging.getLogger("study")

ROUTER_MODULES = [
    "study.api.library",
    "study.api.search",
    "study.api.graph",
    "study.api.study",
    "study.api.chat",
]
LIFECYCLE_MODULES = [
    "study.ingest.worker",
    "study.agent.mcp_server",
    "study.agent.harness",
]


def _optional_import(name: str):
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name == name:
            log.warning("module %s not implemented yet; skipping", name)
            return None
        raise


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and not path.startswith(("api", "mcp")):
                return await super().get_response("index.html", scope)
            raise


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    db.init_db()
    lifecycle = [m for m in (_optional_import(n) for n in LIFECYCLE_MODULES) if m is not None]

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with AsyncExitStack() as stack:
            for mod in lifecycle:
                if hasattr(mod, "lifespan"):
                    await stack.enter_async_context(mod.lifespan(app))
            yield

    app = FastAPI(title="Study App", lifespan=lifespan)
    for name in ROUTER_MODULES:
        mod = _optional_import(name)
        if mod is not None:
            app.include_router(mod.router)
    for mod in lifecycle:
        if hasattr(mod, "mount"):
            mod.mount(app)
    if (config.FRONTEND_DIST / "index.html").exists():
        app.mount("/", SPAStaticFiles(directory=config.FRONTEND_DIST, html=True), name="frontend")
    return app


app = create_app()
