import argparse
import json
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

from .access import new_token
from .config import Settings
from .runtime import Runtime
from .worker import Worker, poll


def main():
    parser = argparse.ArgumentParser(description="Корсик: запуск и обслуживание")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Create .env with a random bootstrap key")
    init.add_argument("--demo", action="store_true")
    sub.add_parser("migrate")
    sub.add_parser("seed-demo")
    sub.add_parser("worker")
    sub.add_parser("poll")
    tick = sub.add_parser("tick")
    tick.add_argument("--limit", type=int, default=100)
    subscribe = sub.add_parser("subscribe")
    subscribe.add_argument("url")
    server = sub.add_parser("serve")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if args.command == "init":
        content = "\n".join(
            [
                "KORSIK_ENVIRONMENT=development",
                f"KORSIK_BOOTSTRAP_TOKEN={new_token()}",
                "KORSIK_DATABASE_URL=sqlite:///./data/korsik.db",
                "KORSIK_DATA_DIR=data",
                "KORSIK_TRANSPORT=mock",
                "KORSIK_ML_MODE=stub",
                f"KORSIK_ENABLE_DEMO={'true' if args.demo else 'false'}",
                f"KORSIK_MAX_WEBHOOK_SECRET={new_token()}",
                "KORSIK_MAX_TOKEN=",
                "KORSIK_MAX_BOT_NAME=",
                "KORSIK_ML_BASE_URL=http://localhost:9000",
                "KORSIK_ML_TOKEN=",
                "",
                f"POSTGRES_PASSWORD={new_token()}",
                "",
            ]
        )
        with Path(".env").open("x", encoding="utf-8") as stream:
            stream.write(content)
        print("Created .env. Keep its keys private. No existing settings were overwritten.")
        return
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if args.command == "migrate":
        root = Path.cwd()
        if not (root / "alembic.ini").is_file():
            raise ValueError("Run migrations from the backend project directory")
        command.upgrade(Config(str(root / "alembic.ini")), "head")
        print("Database migrated.")
        return
    if args.command == "serve":
        import uvicorn

        # Server access logs include query strings; structured application logs redact them.
        uvicorn.run("korsik.api:create_app", factory=True, host=args.host, port=args.port, access_log=False)
        return
    runtime = Runtime(settings)
    runtime.bootstrap()
    try:
        if args.command == "seed-demo":
            from .seed import seed_demo

            print("Demo created. Private credentials: " + str(seed_demo(runtime)))
        elif args.command == "worker":
            Worker(runtime).run()
        elif args.command == "tick":
            print(json.dumps(Worker(runtime).tick(args.limit)))
        elif args.command == "poll":
            poll(runtime)
        elif args.command == "subscribe":
            if not settings.max_webhook_secret:
                raise ValueError("Set KORSIK_MAX_WEBHOOK_SECRET")
            print(runtime.transport.subscribe(args.url, settings.max_webhook_secret))
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
