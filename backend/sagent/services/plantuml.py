"""PlantUML 渲染（選用功能）。

兩種方式，有設定才會啟用：
1. server：自架的 PlantUML 伺服器（例如 docker 的 plantuml/plantuml-server）
2. jar：本機有 Java 和 plantuml.jar

都沒有時，前端只顯示 PlantUML 原始碼。
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import subprocess
import zlib
from collections import OrderedDict
from pathlib import Path

import httpx

_PUML_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_"
_B64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_TO_PUML = str.maketrans(_B64_ALPHABET, _PUML_ALPHABET)


class PlantUMLError(Exception):
    pass


def encode_plantuml(source: str) -> str:
    """PlantUML 伺服器網址用的編碼：raw deflate + PlantUML 自己的 base64 字元表。"""
    import base64

    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    data = compressor.compress(source.encode("utf-8")) + compressor.flush()
    # PlantUML 最後一組不足 3 bytes 時補 0，對應字元是 "0"，等於把 base64 的 "=" 換成 "0"
    return base64.b64encode(data).decode("ascii").translate(_TO_PUML).replace("=", "0")


class PlantUMLRenderer:
    def __init__(self, server_url: str = "", jar_path: Path | None = None, java_cmd: str = "java"):
        self.server_url = server_url.rstrip("/")
        self.jar_path = jar_path
        self.java_cmd = java_cmd
        self._cache: OrderedDict[str, str] = OrderedDict()

    @property
    def mode(self) -> str:
        if self.server_url:
            return "server"
        if self.jar_path and self.jar_path.is_file() and shutil.which(self.java_cmd):
            return "jar"
        return "off"

    @property
    def available(self) -> bool:
        return self.mode != "off"

    async def render_svg(self, source: str) -> str:
        mode = self.mode
        if mode == "off":
            raise PlantUMLError("沒有設定 PlantUML 渲染（需要 PlantUML 伺服器或 Java + plantuml.jar）")
        key = hashlib.sha256(f"{mode}:{source}".encode()).hexdigest()
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        svg = await (self._render_server(source) if mode == "server" else self._render_jar(source))
        self._cache[key] = svg
        if len(self._cache) > 200:
            self._cache.popitem(last=False)
        return svg

    async def _render_server(self, source: str) -> str:
        url = f"{self.server_url}/svg/{encode_plantuml(source)}"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url)
        except httpx.HTTPError as exc:
            raise PlantUMLError(f"連不到 PlantUML 伺服器：{exc}") from exc
        # 語法錯誤時伺服器也會回一張畫著錯誤訊息的 SVG（狀態碼 400）
        if "svg" in resp.headers.get("content-type", "") and resp.text.lstrip().startswith("<"):
            return resp.text
        raise PlantUMLError(f"PlantUML 伺服器回應錯誤：HTTP {resp.status_code}")

    async def _render_jar(self, source: str) -> str:
        def run() -> str:
            proc = subprocess.run(
                [
                    self.java_cmd,
                    "-Djava.awt.headless=true",
                    "-jar",
                    str(self.jar_path),
                    "-tsvg",
                    "-pipe",
                    "-charset",
                    "UTF-8",
                ],
                input=source.encode("utf-8"),
                capture_output=True,
                timeout=60,
            )
            out = proc.stdout.decode("utf-8", errors="replace")
            if not out.lstrip().startswith("<"):
                raise PlantUMLError(
                    proc.stderr.decode("utf-8", errors="replace")[:500] or "PlantUML 沒有輸出"
                )
            return out

        # 用執行緒跑子程序，避免 Windows 上某些事件迴圈不支援 subprocess
        return await asyncio.to_thread(run)
