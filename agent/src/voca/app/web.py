import re
from pathlib import Path

from aiohttp import web
from livekit import api

from voca.config import Settings

ROOM = "voca"
DEVICE_RE = re.compile(r"^[a-z0-9-]{1,32}$")


def default_dist_dir() -> Path:
    return Path(__file__).resolve().parents[4] / "web" / "dist"


def create_app(settings: Settings, dist_dir: Path) -> web.Application:
    index = dist_dir / "index.html"
    if not index.is_file():
        raise FileNotFoundError(
            f"{dist_dir}/index.html not found; build the page: pnpm --dir web build"
        )

    async def healthz(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def token(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except ValueError:
            body = None
        device = body.get("device") if isinstance(body, dict) else None
        if not isinstance(device, str) or not DEVICE_RE.match(device):
            return web.json_response(
                {"error": "device must match ^[a-z0-9-]{1,32}$"}, status=400
            )
        jwt = (
            api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
            .with_identity(f"user-{device}")
            .with_grants(api.VideoGrants(room_join=True, room=ROOM))
            .to_jwt()
        )
        return web.json_response({"token": jwt, "url": settings.public_livekit_url})

    async def page(request: web.Request) -> web.FileResponse:
        return web.FileResponse(index)

    app = web.Application()
    app.router.add_get("/healthz", healthz)
    app.router.add_post("/api/token", token)
    app.router.add_get("/", page)
    assets = dist_dir / "assets"
    if assets.is_dir():
        app.router.add_static("/assets", assets)
    return app
