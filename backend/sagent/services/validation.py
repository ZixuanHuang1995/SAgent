"""把 pydantic 的驗證錯誤轉成給使用者看的中文訊息。

FastAPI 預設的訊息是英文，欄位也是程式裡的名稱（例如「temperature：Input should be less than or equal to 1.5」），
使用者看不懂。這裡把常見的錯誤種類和欄位名稱換成中文，認不得的照原樣顯示。
"""

from __future__ import annotations

from typing import Any

# 欄位的中文名稱。沒列到的欄位顯示原本的名稱
FIELD_LABELS = {
    "temperature": "溫度",
    "num_ctx": "Context 長度",
    "max_revisions": "修訂輪數",
    "ollama_url": "Ollama 網址",
    "default_model": "預設模型",
    "reviewer_model": "審查模型",
    "uml_syntax": "UML 圖語法",
    "diagrams": "畫圖",
    "title": "標題",
    "name": "名稱",
    "requirement": "需求內容",
    "label": "標籤",
}


def _number(v: Any) -> str:
    """1.5 照樣、2.0 寫成 2。"""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _message(err: dict[str, Any]) -> str | None:
    """認得的錯誤種類回傳中文說明（接在欄位名稱後面），認不得的回傳 None。"""
    kind = err.get("type", "")
    ctx = err.get("ctx") or {}
    if kind == "less_than_equal":
        return f"不能大於 {_number(ctx.get('le'))}"
    if kind == "greater_than_equal":
        return f"不能小於 {_number(ctx.get('ge'))}"
    if kind == "less_than":
        return f"要小於 {_number(ctx.get('lt'))}"
    if kind == "greater_than":
        return f"要大於 {_number(ctx.get('gt'))}"
    if kind in ("int_parsing", "int_from_float", "int_type"):
        return "要填整數"
    if kind in ("float_parsing", "float_type"):
        return "要填數字"
    if kind in ("bool_parsing", "bool_type"):
        return "要是 true 或 false"
    if kind == "missing":
        return "必填"
    if kind == "string_too_short":
        return f"至少要 {ctx.get('min_length')} 個字"
    if kind == "string_too_long":
        return f"不能超過 {ctx.get('max_length')} 個字"
    if kind == "literal_error":
        return f"只能是 {str(ctx.get('expected')).replace(' or ', '、').replace(', ', '、')}"
    return None


def readable_errors(errors: list[dict[str, Any]]) -> str:
    """例如「輸入資料不正確：溫度不能大於 1.5」。"""
    problems = []
    for err in errors:
        parts = [str(p) for p in err.get("loc", []) if p not in ("body", "query", "path")]
        field = (FIELD_LABELS.get(parts[-1]) or ".".join(parts)) if parts else ""
        text = _message(err)
        if text is None:
            text = str(err.get("msg", ""))
            problems.append(f"{field}：{text}" if field else text)
        else:
            problems.append(f"{field}{text}" if field else text)
    return "輸入資料不正確：" + "；".join(problems)
