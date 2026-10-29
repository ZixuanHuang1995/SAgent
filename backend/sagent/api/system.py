"""系統狀態、設定、PlantUML 渲染。"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, Response
from fastapi.concurrency import run_in_threadpool

from .. import __version__
from ..agents.llm import ollama_loaded_models, ollama_version
from ..app_state import AppState
from ..schemas import RenderIn
from ..services import settings as settings_service
from ..services.errors import Unavailable
from ..services.plantuml import PlantUMLError
from .deps import get_state

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
async def health(state: AppState = Depends(get_state)) -> dict[str, Any]:
    current = await run_in_threadpool(state.settings)
    ollama: dict[str, Any] = {
        "url": current.ollama_url,
        "reachable": False,
        "version": "",
        "error": "",
        # 已載入的模型和實際的上下文長度。Context 長度設 0 時，設定頁靠這個顯示實際值
        "loaded": [],
    }
    if state.cfg.llm_provider == "ollama":
        try:
            ollama["version"] = await ollama_version(current.ollama_url)
            ollama["reachable"] = True
            async with httpx.AsyncClient(base_url=current.ollama_url.rstrip("/"), timeout=5) as client:
                ollama["loaded"] = await ollama_loaded_models(client)
        except httpx.HTTPError as exc:
            ollama["error"] = str(exc) or exc.__class__.__name__
    return {
        "status": "ok",
        "version": __version__,
        "llm_provider": state.cfg.llm_provider,
        "default_model": current.default_model,
        "ollama": ollama,
        "plantuml": {"mode": state.plantuml.mode, "available": state.plantuml.available},
    }


@router.get("/settings", response_model=settings_service.AppSettings)
def get_settings(state: AppState = Depends(get_state)):
    return state.settings()


@router.put("/settings", response_model=settings_service.AppSettings)
def update_settings(body: settings_service.SettingsPatch, state: AppState = Depends(get_state)):
    with state.db() as s:
        current = settings_service.update_settings(s, state.cfg, body)
    state.apply_settings(current)
    return current


@router.post("/render/plantuml")
async def render_plantuml(body: RenderIn, state: AppState = Depends(get_state)):
    if not state.plantuml.available:
        raise Unavailable(
            "沒有設定 PlantUML 渲染。請在設定頁填 PlantUML 伺服器網址，或安裝 Java 並放 tools/plantuml.jar"
        )
    try:
        svg = await state.plantuml.render_svg(body.source)
    except PlantUMLError as exc:
        raise Unavailable(str(exc)) from exc
    return Response(
        content=svg, media_type="image/svg+xml", headers={"Cache-Control": "private, max-age=3600"}
    )
