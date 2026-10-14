"""把 Spec 打包成 zip：每份 Spec 一個 .md，圖表另外存成 .mmd / .puml。"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .diagrams import extract_diagrams

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(name: str, max_len: int = 80) -> str:
    name = _INVALID_CHARS.sub("_", name).strip().strip(".")
    return (name or "untitled")[:max_len]


@dataclass
class ExportEntry:
    spec_id: int
    phase: str
    title: str
    status: str
    version_no: int
    label: str | None
    content: str


def build_zip(project_name: str, entries: list[ExportEntry], extra: dict[str, Any] | None = None) -> bytes:
    buf = io.BytesIO()
    used: set[str] = set()
    manifest_specs = []
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for entry in entries:
            folder = safe_filename(entry.phase)
            stem = safe_filename(entry.title)
            base_stem, n = stem, 1
            path = f"{folder}/{stem}.md"
            while path.lower() in used:  # 檔名重複時加上編號，直到不重複
                stem = f"{base_stem}-{entry.spec_id}" + (f"-{n}" if n > 1 else "")
                path = f"{folder}/{stem}.md"
                n += 1
            used.add(path.lower())
            zf.writestr(path, entry.content)
            diagram_paths = []
            for i, block in enumerate(extract_diagrams(entry.content), start=1):
                ext = "puml" if block.lang == "plantuml" else "mmd"
                dpath = f"diagrams/{folder}/{stem}-{i}.{ext}"
                zf.writestr(dpath, block.code.strip() + "\n")
                diagram_paths.append(dpath)
            manifest_specs.append(
                {
                    "spec_id": entry.spec_id,
                    "title": entry.title,
                    "phase": entry.phase,
                    "status": entry.status,
                    "version_no": entry.version_no,
                    "label": entry.label,
                    "path": path,
                    "diagrams": diagram_paths,
                }
            )
        manifest = {
            "project": project_name,
            "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            **(extra or {}),
            "specs": manifest_specs,
        }
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return buf.getvalue()
