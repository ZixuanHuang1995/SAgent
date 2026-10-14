"""命令列：sagent serve 啟動服務。"""

from __future__ import annotations

import argparse
import logging
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="sagent", description="SAgent 系統分析代理系統")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="啟動網頁服務")
    serve.add_argument("--host", default=os.environ.get("SAGENT_HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("SAGENT_PORT", "8765")))
    serve.add_argument("--reload", action="store_true", help="開發用：程式改了自動重新載入")
    serve.add_argument("--fake-llm", action="store_true", help="不接模型，用假資料展示流程")
    serve.add_argument("--data-dir", help="資料目錄（資料庫和自訂範本），預設是專案下的 data/")
    args = parser.parse_args()

    if args.command in (None, "serve"):
        host = getattr(args, "host", "127.0.0.1")
        port = getattr(args, "port", 8765)
        if getattr(args, "fake_llm", False):
            os.environ["SAGENT_LLM_PROVIDER"] = "fake"
        if getattr(args, "data_dir", None):
            os.environ["SAGENT_DATA_DIR"] = args.data_dir
        os.environ["SAGENT_HOST"] = host  # 讓設定知道是不是只在本機提供服務
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        uvicorn.run(
            "sagent.main:create_app",
            factory=True,
            host=host,
            port=port,
            reload=getattr(args, "reload", False),
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
