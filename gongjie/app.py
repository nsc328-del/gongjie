from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from gongjie.api.routes import conversations, providers, rooms, worlds
from gongjie.paths import STATIC_DIR
from gongjie.runtime import secrets, store


app = FastAPI(title="共界", docs_url=None, redoc_url=None)
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
    return {"worlds": store.list_worlds(), "provider": public_profile}
