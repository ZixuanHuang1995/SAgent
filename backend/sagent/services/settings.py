"""執行中可在畫面上調整的設定，存在資料庫。沒存過的欄位用啟動設定的預設值。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import Config
from ..models import Setting

_KEY = "app"


class AppSettings(BaseModel):
    ollama_url: str = "http://127.0.0.1:11434"
    default_model: str = "qwen3.6:35b-a3b"
    temperature: float = Field(0.4, ge=0, le=1.5)
    # 審查後最多修訂幾輪。0 = 只審查不修訂
    max_revisions: int = Field(1, ge=0, le=3)
    # auto：預設用 Mermaid；有 PlantUML 渲染時，使用案例圖/元件圖/部署圖改用 PlantUML
    uml_syntax: Literal["auto", "mermaid", "plantuml"] = "auto"
    diagrams: bool = True
    # 0 表示用 Ollama 伺服器的預設值。改這個值會讓 Ollama 重新載入模型
    num_ctx: int = Field(0, ge=0, le=262144)
    plantuml_server: str = ""


class SettingsPatch(BaseModel):
    ollama_url: str | None = None
    default_model: str | None = None
    temperature: float | None = Field(None, ge=0, le=1.5)
    max_revisions: int | None = Field(None, ge=0, le=3)
    uml_syntax: Literal["auto", "mermaid", "plantuml"] | None = None
    diagrams: bool | None = None
    num_ctx: int | None = Field(None, ge=0, le=262144)
    plantuml_server: str | None = None


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
    changes = patch.model_dump(exclude_none=True)
    for key in ("ollama_url", "plantuml_server"):
        if key in changes:
            changes[key] = changes[key].strip().rstrip("/")
    current.update(changes)
    validated = AppSettings.model_validate(current)
    row = s.get(Setting, _KEY)
    if row is None:
        s.add(Setting(key=_KEY, value=validated.model_dump()))
    else:
        row.value = validated.model_dump()
    s.flush()
    return validated
