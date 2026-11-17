from __future__ import annotations

import logging
from pathlib import Path

import click
from apscheduler.schedulers.background import BackgroundScheduler

from itb.app import App
from itb.common import load_config


def run_server(cfg: dict) -> None:
    app = App(config=cfg)
    app.server_status = True
    scheduler = BackgroundScheduler()
    scheduler.add_job(lambda: print("server tick"), "interval", seconds=max(1, int(cfg.get("poll_seconds", 30))))
    scheduler.start()
    logging.info("ITB server started")
    try:
        while True:
            pass
    except KeyboardInterrupt:
        scheduler.shutdown()
        logging.info("ITB server stopped")


@click.command()
@click.option("-c", "--config", "config_path", required=True, type=click.Path(exists=True))
def cli(config_path: str) -> None:
    cfg = load_config(config_path)
    run_server(cfg)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cli()
