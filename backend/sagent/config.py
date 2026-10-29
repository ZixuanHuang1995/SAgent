"""啟動設定：從環境變數讀取，沒有設定就用預設值。

執行中可以調整的設定（模型、溫度等）存在資料庫，見 services/settings.py。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]


@dataclass
class Config:
    data_dir: Path
    db_path: Path
    host: str
    port: int
    # LLM 來源："ollama" 或 "fake"（不接模型，用固定內容模擬，方便測試畫面）
    llm_provider: str
    ollama_url: str
    default_model: str
    plantuml_server: str
    plantuml_jar: Path
    java_cmd: str
    frontend_dist: Path
    # fake LLM 每個字的延遲秒數，模擬串流效果
    fake_delay: float
    # 允許的 Host 標頭（防 DNS rebinding）。["*"] 表示不檢查
    allowed_hosts: list[str]

    @property
    def templates_dir(self) -> Path:
        """使用者自訂文件範本的位置，同名會蓋過內建範本。"""
        return self.data_dir / "templates"


LOOPBACK_HOSTS = ["127.0.0.1", "localhost", "*.localhost"]


def _allowed_hosts(host: str, raw: str) -> list[str]:
    if raw.strip():
        return [h.strip() for h in raw.split(",") if h.strip()]
    # 只在本機提供服務時檢查 Host；開放給其他電腦時（0.0.0.0）不限制，可用 SAGENT_ALLOWED_HOSTS 設定
    return LOOPBACK_HOSTS if host in ("127.0.0.1", "localhost") else ["*"]


def load_config(**overrides) -> Config:
    env = os.environ
    data_dir = Path(env.get("SAGENT_DATA_DIR", ROOT_DIR / "data"))
    host = env.get("SAGENT_HOST", "127.0.0.1")
    cfg = Config(
        data_dir=data_dir,
        db_path=Path(env.get("SAGENT_DB_PATH", data_dir / "sagent.db")),
        host=host,
        port=int(env.get("SAGENT_PORT", "8765")),
        llm_provider=env.get("SAGENT_LLM_PROVIDER", "ollama"),
        ollama_url=env.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        default_model=env.get("SAGENT_DEFAULT_MODEL", "qwen3.6:35b-a3b"),
        plantuml_server=env.get("SAGENT_PLANTUML_SERVER", ""),
        plantuml_jar=Path(env.get("SAGENT_PLANTUML_JAR", ROOT_DIR / "tools" / "plantuml.jar")),
        java_cmd=env.get("SAGENT_JAVA", "java"),
        frontend_dist=Path(env.get("SAGENT_FRONTEND_DIST", ROOT_DIR / "frontend" / "dist")),
        fake_delay=float(env.get("SAGENT_FAKE_DELAY", "0.01")),
        allowed_hosts=_allowed_hosts(host, env.get("SAGENT_ALLOWED_HOSTS", "")),
    )
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg
