"""Command-line orchestration for Loud Gate."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .config import ConfigError, config_path, ensure_app_dir, load_config, log_path, save_config
from .runtime import run_runtime
from .setup import interactive_setup
from .startup import TASK_NAME, install_startup_task, uninstall_startup_task


def setup_logging(verbose: bool, *, reset: bool = False) -> logging.Logger:
    ensure_app_dir()
    logger = logging.getLogger("loud_gate")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    file_handler = logging.FileHandler(log_path(), mode="w" if reset else "a", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(file_handler)

    if verbose:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(stream_handler)

    return logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Loud Gate manager and independent Windows WASAPI microphone runtime."
    )
    parser.add_argument("--manager", action="store_true", help="Open the setup and runtime manager window.")
    parser.add_argument("--runtime", action="store_true", help="Run the audio runtime without opening the manager.")
    parser.add_argument("--background", action="store_true", help="Run runtime mode without console output.")
    parser.add_argument("--elevated", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--autorun", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--setup", action="store_true", help="Re-run interactive device setup.")
    parser.add_argument("--run", action="store_true", help="Compatibility alias for --runtime.")
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

    if args.uninstall_startup:
        try:
            uninstall_startup_task(elevated=args.elevated)
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from exc
        print(f"Removed scheduled task: {TASK_NAME}")
        return 0

    if args.install_startup:
        try:
            install_startup_task(elevated=args.elevated)
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from exc
        print(f"Installed startup task: {TASK_NAME}")
        if not (args.run or args.runtime):
            return 0

    runtime_requested = args.run or args.runtime
    try:
        existing = load_config()
    except ConfigError as exc:
        if runtime_requested:
            logger = setup_logging(False, reset=args.autorun)
            logger.error("Loud Gate could not load its configuration: %s", exc)
            if not args.background:
                print(str(exc), file=sys.stderr)
            return 1
        raise SystemExit(str(exc)) from exc
    if args.setup:
        if not sys.stdin.isatty():
            raise SystemExit(
                "Interactive setup requires a console. Run the script once from PowerShell to set up devices."
            )
        cfg = interactive_setup(existing)
        save_config(cfg)
        print(f"Saved config to {config_path()}")
        existing = cfg
        if not (args.run or args.runtime):
            return 0

    if runtime_requested:
        if existing is None or not existing.has_device_selection:
            message = "No complete device configuration exists. Open the manager or run --setup first."
            logger = setup_logging(False, reset=args.autorun)
            logger.error(message)
            if not args.background:
                print(message, file=sys.stderr)
            return 1
        verbose = bool(args.verbose and not args.background) or (
            sys.stdout.isatty() and not args.quiet and not args.background
        )
        logger = setup_logging(verbose, reset=args.autorun)
        logger.info("Config file: %s", config_path())
        return run_runtime(existing, logger, verbose)

    from .manager import run_manager

    return run_manager()
