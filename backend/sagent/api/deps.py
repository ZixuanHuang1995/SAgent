from __future__ import annotations

from urllib.parse import quote

from fastapi import Request
from fastapi.responses import Response

from ..app_state import AppState


def get_state(request: Request) -> AppState:
    return request.app.state.sagent


def download(data: bytes | str, filename: str, media_type: str) -> Response:
    """檔名可能有中文，用 RFC 5987 的 filename* 格式。"""
    body = data.encode("utf-8") if isinstance(data, str) else data
    ascii_name = filename.encode("ascii", "ignore").decode() or "download"
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
    return Response(content=body, media_type=media_type, headers={"Content-Disposition": disposition})
