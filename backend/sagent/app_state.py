"""整個應用程式共用的物件：資料庫、設定、範本、AI 工作執行器。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy.orm import Session

from .agents.doc_templates import DocTemplate, load_templates
from .agents.fake_llm import FakeLLM
from .agents.llm import LLMClient, OllamaClient
from .agents.runner import LLMFactory, RunManager
from .config import Config
from .db import Base, make_engine, make_session_factory, session_scope
from .migrations import migrate
from .services import settings as settings_service
from .services.plantuml import PlantUMLRenderer


class AppState:
    def __init__(self, cfg: Config, llm_factory: LLMFactory | None = None):
        self.cfg = cfg
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        self.engine = make_engine(cfg.db_path)
        Base.metadata.create_all(self.engine)
        migrate(self.engine)
        self.session_factory = make_session_factory(self.engine)
        with self.db() as s:
            current = settings_service.get_settings(s, cfg)
        self.plantuml = PlantUMLRenderer(current.plantuml_server, cfg.plantuml_jar, cfg.java_cmd)
        self.runner = RunManager(
            self.session_factory,
            llm_factory or self.default_llm_factory,
            self.templates,
            lambda: self.plantuml.available,
        )

    @contextmanager
    def db(self) -> Iterator[Session]:
        with session_scope(self.session_factory) as s:
            yield s

    def templates(self) -> dict[str, DocTemplate]:
        # 每次重新讀檔，使用者改了範本不用重開服務
        return load_templates(self.cfg.templates_dir)

    def settings(self) -> settings_service.AppSettings:
        with self.db() as s:
            return settings_service.get_settings(s, self.cfg)

    def apply_settings(self, current: settings_service.AppSettings) -> None:
        self.plantuml.server_url = current.plantuml_server.rstrip("/")

    def default_llm_factory(self, model: str, opts: dict[str, Any]) -> LLMClient:
        if self.cfg.llm_provider == "fake":
            return FakeLLM(model=model, delay=self.cfg.fake_delay)
        return OllamaClient(
            opts.get("ollama_url") or self.cfg.ollama_url,
            model,
            num_ctx=int(opts.get("num_ctx") or 0),
        )
