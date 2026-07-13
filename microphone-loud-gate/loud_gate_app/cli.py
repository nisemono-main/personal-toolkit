"""Command-line orchestration for Loud Gate."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .config import ConfigError, config_path, ensure_app_dir, load_config, log_path, save_config
from .runtime import run_service
from .setup import interactive_setup
from .startup import TASK_NAME, install_startup_task, is_windows_admin, relaunch_as_admin, uninstall_startup_task


def setup_logging(verbose: bool) -> logging.Logger:
    ensure_app_dir()
    logger = logging.getLogger("loud_gate")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    file_handler = logging.FileHandler(log_path(), encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(file_handler)

    if verbose:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(stream_handler)

    return logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Windows lookahead mic limiter with configurable global hotkeys."
    )
    parser.add_argument("--setup", action="store_true", help="Re-run interactive device setup.")
    parser.add_argument("--run", action="store_true", help="Run without prompting. Requires saved config.")
    parser.add_argument(
        "--install-startup",
        action="store_true",
        help="Create a logon scheduled task so the script starts automatically.",
    )
    parser.add_argument(
        "--uninstall-startup",
        action="store_true",
        help="Remove the logon scheduled task.",
    )
    parser.add_argument("--verbose", action="store_true", help="Print live status to the console.")
    parser.add_argument("--quiet", action="store_true", help="Suppress console status output.")
    return parser.parse_args()


def main() -> int:
    if os.name != "nt":
        raise SystemExit("This script is Windows-only.")

    args = parse_args()
    did_setup = False

    if args.install_startup or args.uninstall_startup:
        if not is_windows_admin():
            relaunch_as_admin(sys.argv[1:])
            return 0

    if args.uninstall_startup:
        uninstall_startup_task()
        print(f"Removed scheduled task: {TASK_NAME}")
        return 0

    try:
        existing = load_config()
    except ConfigError as exc:
        raise SystemExit(str(exc)) from exc
    cfg = existing

    if args.setup or cfg is None or not cfg.has_device_selection:
        if not sys.stdin.isatty():
            raise SystemExit(
                "Interactive setup requires a console. Run the script once from PowerShell to set up devices."
            )
        cfg = interactive_setup(existing)
        save_config(cfg)
        print(f"Saved config to {config_path()}")
        did_setup = True

    if args.install_startup:
        install_startup_task()
        print(f"Installed startup task: {TASK_NAME}")
        if not args.run:
            return 0

    verbose = args.verbose or (sys.stdout.isatty() and not args.quiet)
    logger = setup_logging(verbose)
    logger.info("Config file: %s", config_path())

    if did_setup and not args.run:
        print(
            "Setup complete. Run `python .\\loud_gate.py --run` to start it now, "
            "or use `--install-startup` to launch automatically."
        )
        return 0

    run_service(cfg, logger, verbose)
    return 0
