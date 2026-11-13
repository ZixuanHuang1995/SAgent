"""系統狀態、設定、PlantUML 渲染。"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from .. import __version__
from ..agents.llm import ollama_list_models, ollama_loaded_models, ollama_version
from ..agents.openai_llm import cloud_list_models, error_detail, status_message
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
    cloud = settings_service.uses_cloud(state.cfg, current)
    # 用雲端 API 時不檢查 Ollama（沒開也沒關係）。雲端這裡也不連：這個 API 畫面常常呼叫，
    # 要確認連得到用設定頁的「測試連線」（POST /api/cloud/check）
    if state.cfg.llm_provider == "ollama" and not cloud:
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
        # 模型來源：ollama 本機、cloud 雲端 API。示範模式（llm_provider 是 fake）時一律不用雲端
        "llm_source": "cloud" if cloud else "ollama",
        # 雲端 API：有沒有設定網址和金鑰（金鑰本身不回傳）、網址、目前選的模型
        "cloud": {
            "configured": state.cfg.cloud_ready,
            "base_url": state.cfg.cloud_base_url,
            "model": current.cloud_model,
        },
        "ollama": ollama,
        "plantuml": {"mode": state.plantuml.mode, "available": state.plantuml.available},
        # 設定頁顯示目前用的是哪個資料資料夾（data/ 或 data-demo/），免得在示範資料裡以為在改正式設定
        "data_dir": str(state.cfg.data_dir.resolve()),
    }


class OllamaCheckIn(BaseModel):
    url: str


def _connect_error(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "等太久沒有回應，請確認 Ollama 已經啟動"
    if isinstance(exc, httpx.ConnectError):
        return "連不到，請確認 Ollama 已經啟動、網址和埠號正確"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"回應錯誤（HTTP {exc.response.status_code}），這個網址可能不是 Ollama"
    if isinstance(exc, ValueError):
        return "回應的格式不對，這個網址可能不是 Ollama"
    return str(exc) or exc.__class__.__name__


@router.post("/ollama/check")
async def check_ollama(body: OllamaCheckIn) -> dict[str, Any]:
    """設定頁的「測試連線」：用還沒存檔的網址查 Ollama 版本和模型清單，不改設定。"""
    url = body.url.strip().rstrip("/")
    settings_service.check_http_url(url, "Ollama 網址")
    result: dict[str, Any] = {"url": url, "reachable": False, "version": "", "error": "", "models": []}
    try:
        result["version"] = await ollama_version(url)
        result["models"] = await ollama_list_models(url)
        result["reachable"] = True
    except (httpx.HTTPError, ValueError) as exc:
        result["error"] = _connect_error(exc)
    return result


def _cloud_error(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "等太久沒有回應，請確認網址正確、連得到外部網路"
    if isinstance(exc, httpx.ConnectError):
        return "連不到，請確認 SAGENT_CLOUD_BASE_URL 的網址正確、連得到外部網路"
    if isinstance(exc, httpx.HTTPStatusError):
        return status_message(exc.response.status_code, error_detail(exc.response.text))
    if isinstance(exc, ValueError):
        return "回應的格式不對，這個網址可能不是 OpenAI 相容的 API"
    return str(exc) or exc.__class__.__name__


@router.post("/cloud/check")
async def check_cloud(state: AppState = Depends(get_state)) -> dict[str, Any]:
    """設定頁的「測試連線」：用環境變數的網址和金鑰查雲端 API 有哪些模型。

    網址不能從畫面帶進來：金鑰會跟著送出去，沒有登入功能時等於任何人都能把金鑰騙走。
    """
    cfg = state.cfg
    result: dict[str, Any] = {
        "configured": cfg.cloud_ready,
        "base_url": cfg.cloud_base_url,
        "reachable": False,
        "error": "",
        "note": "",
        "models": [],
    }
    if not cfg.cloud_ready:
        result["error"] = settings_service.CLOUD_NOT_READY
        return result
    try:
        result["models"] = await cloud_list_models(cfg.cloud_base_url, cfg.cloud_api_key)
        result["reachable"] = True
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            # 有些相容服務沒有 /models，不影響撰寫
            result["reachable"] = True
            result["note"] = "這個服務不提供模型清單，模型名稱要自己填"
        else:
            result["error"] = _cloud_error(exc).replace(cfg.cloud_api_key, "***")
    except (httpx.HTTPError, ValueError) as exc:
        result["error"] = _cloud_error(exc).replace(cfg.cloud_api_key, "***")
    return result


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
            "沒有設定 PlantUML 渲染。請用環境變數 SAGENT_PLANTUML_SERVER 指定自架的 PlantUML 伺服器，"
            "或安裝 Java 並放 tools/plantuml.jar，再重新啟動 SAgent"
        )
    try:
        svg = await state.plantuml.render_svg(body.source)
    except PlantUMLError as exc:
        raise Unavailable(str(exc)) from exc
    return Response(
        content=svg, media_type="image/svg+xml", headers={"Cache-Control": "private, max-age=3600"}
    )
