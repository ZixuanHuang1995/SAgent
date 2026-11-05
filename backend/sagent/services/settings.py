"""執行中可在畫面上調整的設定，存在資料庫。沒存過的欄位用啟動設定的預設值。"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import Config
from ..models import Setting
from .errors import BadRequest

_KEY = "app"
# 可以設成 null 的欄位：PUT 送 null 表示清掉（其他欄位送 null 表示不改）
_NULLABLE = {"seed"}


class AppSettings(BaseModel):
    ollama_url: str = "http://127.0.0.1:11434"
    default_model: str = "qwen3.6:35b-a3b"
    # 審查用的模型。空白 = 跟撰寫用同一個。用不同的模型審查，比較不會偏好自己寫的內容
    reviewer_model: str = ""
    temperature: float = Field(0.4, ge=0, le=1.5)
    # 審查後最多修訂幾輪。0 = 只審查不修訂
    max_revisions: int = Field(2, ge=0, le=3)
    # auto：預設用 Mermaid；有 PlantUML 渲染時，使用案例圖/元件圖/部署圖改用 PlantUML
    uml_syntax: Literal["auto", "mermaid", "plantuml"] = "auto"
    diagrams: bool = True
    # 0 表示用 Ollama 伺服器的預設值。改這個值會讓 Ollama 重新載入模型
    num_ctx: int = Field(0, ge=0, le=262144)
    plantuml_server: str = ""
    # 整個 AI 工作的時間上限（分鐘），超過就標失敗。0 = 不限制（研究計劃 C-06）
    max_run_minutes: float = Field(0, ge=0, le=1440)
    # 固定的亂數種子（研究計劃 C-08）。只在回歸測試用，正式撰寫留空（None）
    seed: int | None = Field(None, ge=0, le=2**31 - 1)
    # 每一步的提示詞全文存進工作紀錄（C-09）。資料庫會變大、內容含需求原文；關閉時只存雜湊
    store_prompts: bool = False
    # 實驗（C-19）：Writer 的章節提示詞加上人改過的同一節當範例（<資料資料夾>/examples/<文件類型>/<章節>.md）
    fewshot_examples: bool = False


class SettingsPatch(BaseModel):
    ollama_url: str | None = None
    default_model: str | None = None
    reviewer_model: str | None = None
    temperature: float | None = Field(None, ge=0, le=1.5)
    max_revisions: int | None = Field(None, ge=0, le=3)
    uml_syntax: Literal["auto", "mermaid", "plantuml"] | None = None
    diagrams: bool | None = None
    num_ctx: int | None = Field(None, ge=0, le=262144)
    plantuml_server: str | None = None
    max_run_minutes: float | None = Field(None, ge=0, le=1440)
    seed: int | None = Field(None, ge=0, le=2**31 - 1)
    store_prompts: bool | None = None
    fewshot_examples: bool | None = None


def check_http_url(url: str, label: str) -> None:
    """網址要是 http:// 或 https:// 開頭、後面有主機名稱。"""
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise BadRequest(f"{label}要用 http:// 或 https:// 開頭，例如 http://127.0.0.1:11434")


def _defaults(cfg: Config) -> AppSettings:
    return AppSettings(
        ollama_url=cfg.ollama_url,
        default_model=cfg.default_model,
        plantuml_server=cfg.plantuml_server,
    )


def get_settings(s: Session, cfg: Config) -> AppSettings:
    row = s.get(Setting, _KEY)
    data = _defaults(cfg).model_dump()
    if row is not None and isinstance(row.value, dict):
        data.update({k: v for k, v in row.value.items() if k in data})
    return AppSettings.model_validate(data)


def update_settings(s: Session, cfg: Config, patch: SettingsPatch) -> AppSettings:
    current = get_settings(s, cfg).model_dump()
    changes = {
        k: v for k, v in patch.model_dump(exclude_unset=True).items() if v is not None or k in _NULLABLE
    }
    for key in ("ollama_url", "plantuml_server"):
        if key in changes:
            changes[key] = changes[key].strip().rstrip("/")
    if "ollama_url" in changes:
        check_http_url(changes["ollama_url"], "Ollama 網址")
    current.update(changes)
    validated = AppSettings.model_validate(current)
    row = s.get(Setting, _KEY)
    if row is None:
        s.add(Setting(key=_KEY, value=validated.model_dump()))
    else:
        row.value = validated.model_dump()
    s.flush()
    return validated
