"""資料庫升級。

新資料表由 create_all 建立；已經存在的資料表加欄位要在這裡處理。
每個步驟都要能重複執行（已經升級過就什麼都不做）。
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, text

from .services.specs import line_stats

log = logging.getLogger(__name__)


def _columns(conn, table: str) -> set[str]:
    return {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}


def _add_version_line_stats(engine: Engine) -> None:
    """spec_versions 加上 lines_added / lines_removed，並補算舊版本的數字。"""
    with engine.begin() as conn:
        cols = _columns(conn, "spec_versions")
        for name in ("lines_added", "lines_removed"):
            if name not in cols:
                conn.execute(text(f"ALTER TABLE spec_versions ADD COLUMN {name} INTEGER"))

        spec_ids = [
            r[0] for r in conn.execute(text("SELECT DISTINCT spec_id FROM spec_versions WHERE lines_added IS NULL"))
        ]
        if not spec_ids:
            return
        log.info("補算 %d 份 Spec 的版本變更行數", len(spec_ids))
        for spec_id in spec_ids:
            rows = conn.execute(
                text(
                    "SELECT id, content, lines_added FROM spec_versions WHERE spec_id = :sid ORDER BY version_no"
                ),
                {"sid": spec_id},
            ).all()
            previous = ""
            for row_id, content, done in rows:
                if done is None:
                    added, removed = line_stats(previous, content)
                    conn.execute(
                        text("UPDATE spec_versions SET lines_added = :a, lines_removed = :r WHERE id = :id"),
                        {"a": added, "r": removed, "id": row_id},
                    )
                previous = content


def migrate(engine: Engine) -> None:
    _add_version_line_stats(engine)
