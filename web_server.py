"""Vue 生产页面与 WebSocket 游戏服务器统一启动入口。"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import uvicorn

from arena.web import create_app

BASE_DIR = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(description="BLACKLINE Vue + FastAPI 游戏服务器")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址；部署时通常使用 0.0.0.0")
    parser.add_argument("--port", type=int, default=8000, help="HTTP / WebSocket 统一端口")
    parser.add_argument("--db", default=str(BASE_DIR / "data" / "arena.db"), help="SQLite 数据库路径")
    parser.add_argument("--tick-rate", type=int, default=30, help="权威逻辑帧率")
    args = parser.parse_args()
    if not 1 <= args.tick_rate <= 120:
        parser.error("tick-rate 应在 1～120 之间")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app = create_app(
        db_path=args.db,
        static_dir=BASE_DIR / "web" / "dist",
        tick_rate=args.tick_rate,
    )
    uvicorn.run(app, host=args.host, port=args.port, ws="websockets", log_level="info")


if __name__ == "__main__":
    main()
