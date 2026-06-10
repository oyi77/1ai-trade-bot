"""Entry point: python -m tradebot.web"""
import argparse

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="1ai-trade-bot Web Dashboard")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", type=int, default=9090, help="Bind port")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on changes")
    args = parser.parse_args()

    print(f"📡 1ai-trade-bot Dashboard → http://{args.host}:{args.port}")
    uvicorn.run(
        "tradebot.web.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
