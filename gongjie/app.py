from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from gongjie.api.routes import conversations, providers, rooms, worlds
from gongjie.ops.access import PasswordAccess
from gongjie.paths import STATIC_DIR
from gongjie.runtime import access_config, current_space, secrets, space_resource, store
from gongjie.storage.worldbooks import public_templates


app = FastAPI(title="共界", docs_url=None, redoc_url=None)
app.add_middleware(PasswordAccess)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(worlds.router)
app.include_router(conversations.router)
app.include_router(providers.router)
app.include_router(rooms.router)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/bootstrap")
async def bootstrap() -> dict:
    try:
        profile = store.active_provider()
    except KeyError:
        profile = None
    public_profile = {**profile, "has_key": secrets.has(profile["id"])} if profile else None
    return {"worlds": store.list_worlds(), "provider": public_profile, "templates": public_templates(), "access_enabled": bool(access_config), "space_id": current_space.get() or "local", "space_label": space_resource("label")}
