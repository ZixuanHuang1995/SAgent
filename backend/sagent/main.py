"""FastAPI 應用程式。前端打包後的檔案（frontend/dist）也由這裡提供。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .agents.runner import LLMFactory
from .api import agent, projects, specs, system
from .app_state import AppState
from .config import Config, load_config
from .services.errors import ServiceError

log = logging.getLogger("sagent")

# 修改資料的 API 都要帶這個標頭。其他網站的表單送不出自訂標頭，跨站 fetch 也會被瀏覽器擋下（CSRF 防護）
CLIENT_HEADER = "x-sagent-client"
_SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def create_app(cfg: Config | None = None, *, llm_factory: LLMFactory | None = None) -> FastAPI:
    cfg = cfg or load_config()
    state = AppState(cfg, llm_factory=llm_factory)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await state.runner.start()
        try:
            yield
        finally:
            await state.runner.stop()
            state.engine.dispose()

    app = FastAPI(title="SAgent 系統分析代理系統", version=__version__, lifespan=lifespan)
    app.state.sagent = state

    @app.middleware("http")
    async def _require_client_header(request: Request, call_next):
        if (
            request.method not in _SAFE_METHODS
            and request.url.path.startswith("/api/")
            and not request.headers.get(CLIENT_HEADER)
        ):
            return JSONResponse(
                {"detail": "請從 SAgent 網頁操作（缺少 X-SAgent-Client 標頭）"}, status_code=403
            )
        return await call_next(request)

    if cfg.allowed_hosts != ["*"]:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=cfg.allowed_hosts)

    @app.exception_handler(ServiceError)
    async def _service_error(_request: Request, exc: ServiceError):
        return JSONResponse({"detail": exc.message}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError):
        problems = []
        for err in exc.errors():
            field = ".".join(str(p) for p in err.get("loc", []) if p != "body")
            problems.append(f"{field}：{err.get('msg', '')}" if field else err.get("msg", ""))
        return JSONResponse({"detail": "輸入資料不正確：" + "；".join(problems)}, status_code=422)

    for module in (projects, specs, agent, system):
        app.include_router(module.router)

    dist = cfg.frontend_dist
    if (dist / "index.html").is_file():
        if (dist / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            if full_path.startswith("api/"):
                return JSONResponse({"detail": "找不到這個 API"}, status_code=404)
            candidate = (dist / full_path).resolve()
            if full_path and candidate.is_file() and dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            # index.html 每次都要重新確認，前端更新後瀏覽器才會載入新的 assets（assets 檔名有雜湊，可以快取）
            return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})
    else:
        log.warning("找不到前端檔案 %s，只提供 API。請先在 frontend 目錄執行 npm run build", dist)

    return app
