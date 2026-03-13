"""DeployTool — build Jekyll site and serve it via a static file server.

Includes a git-polling watcher that auto-rebuilds when content changes upstream.
"""

from __future__ import annotations

import logging
import shutil
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import click

from repo_tools.core import RepoTool, ToolContext, logger, resolve_path

_DEFAULT_POLL_INTERVAL = 300


class DeployTool(RepoTool):
    name = "deploy"
    help = "Build Jekyll site and start static file server"
    deps = ["waitress"]

    def setup(self, cmd: click.Command) -> click.Command:
        cmd = click.option(
            "--port", type=int, default=None, help="Port for the static file server (default: 8082)"
        )(cmd)
        cmd = click.option(
            "--host", default=None, help="Host to bind the static file server (default: 0.0.0.0)"
        )(cmd)
        cmd = click.option(
            "--skip-build", is_flag=True, default=False, help="Skip Jekyll build step"
        )(cmd)
        cmd = click.option(
            "--watch/--no-watch",
            default=None,
            help="Poll git for changes and auto-rebuild (default: on)",
        )(cmd)
        cmd = click.option(
            "--poll-interval",
            type=int,
            default=None,
            help=f"Seconds between git poll checks (default: {_DEFAULT_POLL_INTERVAL})",
        )(cmd)
        return cmd

    def default_args(self, tokens: dict[str, str]) -> dict[str, Any]:
        return {
            "port": 8082,
            "host": "0.0.0.0",
            "skip_build": False,
            "watch": True,
            "poll_interval": _DEFAULT_POLL_INTERVAL,
        }

    def execute(self, ctx: ToolContext, args: dict[str, Any]) -> None:
        workspace = ctx.workspace_root
        site_dir = workspace / "_site"
        port: int = args["port"]
        host: str = args["host"]
        skip_build: bool = args["skip_build"]
        watch: bool = args["watch"]
        poll_interval: int = args["poll_interval"]

        # ── 0. Logging ─────────────────────────────────────────────
        log_dir = resolve_path(
            workspace,
            ctx.tool_config.get("log_dir", "{workspace_root}/_log"),
            ctx.tokens,
        )
        _setup_file_logging(log_dir)

        # ── 1. Build ────────────────────────────────────────────────
        if not skip_build:
            _build(workspace, atomic=False)

        if not site_dir.is_dir():
            logger.error(f"Site directory not found: {site_dir}")
            sys.exit(1)

        # ── 2. Git watcher ─────────────────────────────────────────
        shutdown = threading.Event()

        if watch:
            watcher = threading.Thread(
                target=_git_watch_loop,
                args=(workspace, poll_interval, shutdown),
                daemon=True,
            )
            watcher.start()
            logger.info(f"Git watcher active, polling every {poll_interval}s")

        # ── 3. Static file server (blocking) ──────────────────────
        def on_signal(signum, frame):
            logger.info("Shutting down...")
            shutdown.set()
            sys.exit(0)

        signal.signal(signal.SIGINT, on_signal)
        signal.signal(signal.SIGTERM, on_signal)

        logger.info(f"Serving {site_dir} on {host}:{port}")
        _serve_static(site_dir, host, port)


# ── Logging ──────────────────────────────────────────────────────────


def _setup_file_logging(log_dir: Path) -> None:
    from logging.handlers import RotatingFileHandler

    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "deploy.log"

    handler = RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )
    logger.addHandler(handler)
    logger.info(f"Logging to {log_file}")


# ── Build ────────────────────────────────────────────────────────────


def _build(workspace: Path, atomic: bool = True) -> bool:
    site_dir = workspace / "_site"
    staging_dir = workspace / "_site_staging"

    if not atomic:
        logger.info("Building Jekyll site...")
        result = subprocess.run(
            "bundle exec jekyll build", shell=True, cwd=workspace,
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            logger.error(f"Jekyll build failed:\n{result.stderr or result.stdout}")
            return False
        logger.info("Build complete")
        return True

    logger.info("Building Jekyll site (staging)...")
    if staging_dir.exists():
        shutil.rmtree(staging_dir)

    result = subprocess.run(
        f"bundle exec jekyll build -d {staging_dir}",
        shell=True, cwd=workspace,
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        logger.error(f"Jekyll build failed, keeping previous site intact:\n{result.stderr or result.stdout}")
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        return False

    old_dir = workspace / "_site_old"
    if old_dir.exists():
        shutil.rmtree(old_dir)

    if site_dir.exists():
        site_dir.rename(old_dir)
    staging_dir.rename(site_dir)

    if old_dir.exists():
        shutil.rmtree(old_dir)

    logger.info("Build complete (swapped)")
    return True


# ── Git watcher ──────────────────────────────────────────────────────


def _git_watch_loop(
    workspace: Path, interval: int, shutdown: threading.Event
) -> None:
    while not shutdown.is_set():
        if shutdown.wait(interval):
            break
        try:
            if _git_has_updates(workspace):
                logger.info("Upstream changes detected, pulling and rebuilding...")
                _git_pull(workspace)
                _build(workspace)
        except Exception as exc:
            logger.warning(f"Git watch error: {exc}")


def _git_has_updates(workspace: Path) -> bool:
    subprocess.run(
        ["git", "fetch", "origin"], cwd=workspace, capture_output=True,
    )
    log_result = subprocess.run(
        ["git", "log", "HEAD..origin/master", "--oneline"],
        cwd=workspace, capture_output=True, text=True,
    )
    return bool(log_result.stdout.strip())


def _git_pull(workspace: Path) -> None:
    result = subprocess.run(
        ["git", "pull", "--ff-only", "origin", "master"],
        cwd=workspace, capture_output=True, text=True,
    )
    if result.returncode != 0:
        logger.warning(f"git pull failed: {result.stderr}")
    else:
        logger.info(f"Pulled: {result.stdout.strip()}")


# ── Static server ────────────────────────────────────────────────────


def _serve_static(site_dir: Path, host: str, port: int) -> None:
    from waitress import serve as waitress_serve

    app = _StaticApp(site_dir)
    logger.info(f"Static server listening on {host}:{port}")
    waitress_serve(app, host=host, port=port)


class _StaticApp:
    def __init__(self, root: Path):
        self.root = root

    def __call__(self, environ, start_response):
        raw_path = environ.get("PATH_INFO", "/")
        path = raw_path.encode("latin-1").decode("utf-8", errors="replace")
        path = unquote(path).lstrip("/")
        if not path:
            path = "index.html"

        file_path = self.root / path

        if file_path.is_dir():
            file_path = file_path / "index.html"

        if not file_path.exists() and not file_path.suffix:
            for candidate in [
                file_path.with_suffix(".html"),
                file_path / "index.html",
            ]:
                if candidate.exists():
                    file_path = candidate
                    break

        if not file_path.exists() or not file_path.is_file():
            start_response("404 Not Found", [("Content-Type", "text/plain")])
            return [b"404 Not Found"]

        try:
            file_path.resolve().relative_to(self.root.resolve())
        except ValueError:
            start_response("403 Forbidden", [("Content-Type", "text/plain")])
            return [b"403 Forbidden"]

        content_type = _guess_type(file_path)
        body = file_path.read_bytes()
        start_response(
            "200 OK",
            [
                ("Content-Type", content_type),
                ("Content-Length", str(len(body)),)
            ],
        )
        return [body]


def _guess_type(path: Path) -> str:
    types = {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
        ".webp": "image/webp",
        ".ico": "image/x-icon",
        ".woff": "font/woff",
        ".woff2": "font/woff2",
        ".ttf": "font/ttf",
        ".xml": "application/xml",
        ".txt": "text/plain; charset=utf-8",
        ".pdf": "application/pdf",
    }
    return types.get(path.suffix.lower(), "application/octet-stream")
