"""記錄這次 AI 工作用的是哪一版提示詞和範本。

改了提示詞或範本之後，要比較前後的結果，得先知道每一次工作是用哪一版跑的（`scripts/analyze_runs.py`
會依這個分組）。工作紀錄只存輸出，不存提示詞，所以在工作開始時記下這三個值：

- prompts：`prompts.py`、`diagram_kinds.py` 的內容雜湊（提示詞和圖的範例都在這兩個檔案）
- template：這次用的文件範本內容的雜湊（自訂範本改了也看得出來）
- code：git commit；`backend/sagent` 有還沒 commit 的修改時後面加「+」；不是 git 目錄或沒有 git 時是空字串
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

from ..config import ROOT_DIR
from .doc_templates import DocTemplate

_AGENTS_DIR = Path(__file__).resolve().parent
PROMPT_FILES = ("prompts.py", "diagram_kinds.py")


_CODE_CACHE_SECONDS = 30
_code_cache: tuple[float, str] | None = None


def prompts_hash() -> str:
    h = hashlib.sha256()
    for name in PROMPT_FILES:
        # 換行統一成 LF：同一份檔案在 Windows 和 git 裡的換行可能不同
        h.update((_AGENTS_DIR / name).read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:12]


def template_hash(template: DocTemplate) -> str:
    data = json.dumps(asdict(template), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:12]


def _git(*args: str) -> str:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # type: ignore[attr-defined]
    result = subprocess.run(
        ["git", *args], cwd=ROOT_DIR, capture_output=True, text=True, timeout=3, creationflags=flags
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def code_version() -> str:
    """git 要另外開程式，結果留 30 秒（AI 工作一份要幾十分鐘，不會漏記）。"""
    global _code_cache
    now = time.monotonic()
    if _code_cache is not None and now - _code_cache[0] < _CODE_CACHE_SECONDS:
        return _code_cache[1]
    try:
        commit = _git("rev-parse", "--short", "HEAD")
        dirty = _git("status", "--porcelain", "--", "backend/sagent") if commit else ""
        value = commit + ("+" if dirty else "") if commit else ""
    except (OSError, subprocess.SubprocessError):
        value = ""
    _code_cache = (now, value)
    return value


def prompt_version(template: DocTemplate) -> dict[str, str]:
    return {"prompts": prompts_hash(), "template": template_hash(template), "code": code_version()}
