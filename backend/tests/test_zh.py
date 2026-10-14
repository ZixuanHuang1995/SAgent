from sagent.agents.zh import to_traditional


def test_converts_only_simplified_only_characters():
    assert to_traditional("人資可將查詢結果汇出為 Excel") == "人資可將查詢結果匯出為 Excel"
    assert to_traditional("软件、头发、发送通知") == "軟件、頭髮、發送通知"


def test_keeps_traditional_text_unchanged():
    text = "干擾、准許、通過、權限、台灣、後台、著作、程序、皇后、乾燥"
    assert to_traditional(text) == text


def test_keeps_code_and_ascii():
    code = '```mermaid\nflowchart TD\n    A["数据库"] --> B\n```'
    assert to_traditional(code) == '```mermaid\nflowchart TD\n    A["數據庫"] --> B\n```'
    assert to_traditional("") == ""
    assert to_traditional('{"a": 1}') == '{"a": 1}'
