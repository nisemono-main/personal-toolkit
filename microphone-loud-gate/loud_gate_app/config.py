"""Configuration models, validation, migration, and persistence for Loud Gate."""

from __future__ import annotations

import configparser
import math
import os
from dataclasses import dataclass, fields
from pathlib import Path


APP_NAME = "loud-gate"
CONFIG_VERSION = 5
CONFIG_SECTION = "loud-gate"
HOTKEY_CONFIG_SECTION = "hotkeys"

DEFAULT_THRESHOLD_DB = -18.0
DEFAULT_RELEASE_MS = 150.0
DEFAULT_LOOKAHEAD_MS = 25.0
DEFAULT_SAMPLE_RATE = 44100
MIN_THRESHOLD_DB = -120.0
MAX_THRESHOLD_DB = 0.0


class ConfigError(RuntimeError):
    """Raised when the persisted configuration cannot be used safely."""


@dataclass(slots=True)
class LoudGateConfig:
    """Validated runtime settings loaded from the Loud Gate INI file."""

    version: int = CONFIG_VERSION
    input_device_index: int | None = None
    input_device_name: str | None = None
    input_device_hostapi: str | None = None
    output_device_index: int | None = None
    output_device_name: str | None = None
    output_device_hostapi: str | None = None
    threshold_db: float = DEFAULT_THRESHOLD_DB
    release_ms: float = DEFAULT_RELEASE_MS
    lookahead_ms: float = DEFAULT_LOOKAHEAD_MS
    mute_hotkey: str = "F13"
    stop_hotkey: str = "CTRL+SHIFT+F13"
    threshold_down_hotkey: str = "F14"
    threshold_up_hotkey: str = "CTRL+F14"
    threshold_step_db: float = 5.0

    @property
    def has_device_selection(self) -> bool:
        return all(
            value is not None
            for value in (
                self.input_device_index,
                self.input_device_name,
                self.output_device_index,
                self.output_device_name,
            )
        )

    def validate(self, *, require_devices: bool = False) -> None:
        if self.version != CONFIG_VERSION:
            raise ConfigError(
                f"Unsupported configuration version {self.version}; expected {CONFIG_VERSION}."
            )

        for field_name in ("input_device_index", "output_device_index"):
            value = getattr(self, field_name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ConfigError(f"{field_name} must be a non-negative integer or blank.")

        for field_name in (
            "input_device_name",
            "input_device_hostapi",
            "output_device_name",
            "output_device_hostapi",
        ):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ConfigError(f"{field_name} must be a non-empty string or blank.")

        for field_name in ("threshold_db", "release_ms", "lookahead_ms", "threshold_step_db"):
            value = getattr(self, field_name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                raise ConfigError(f"{field_name} must be a finite number.")

        if self.release_ms < 0:
            raise ConfigError("release_ms cannot be negative.")
        if self.lookahead_ms < 0:
            raise ConfigError("lookahead_ms cannot be negative.")
        if self.threshold_step_db <= 0:
            raise ConfigError("threshold_step_db must be greater than zero.")
        if not (MIN_THRESHOLD_DB <= self.threshold_db <= MAX_THRESHOLD_DB):
            raise ConfigError(
                f"threshold_db must be between {MIN_THRESHOLD_DB:g} dBFS and {MAX_THRESHOLD_DB:g} dBFS."
            )

        for field_name in (
            "mute_hotkey",
            "stop_hotkey",
            "threshold_down_hotkey",
            "threshold_up_hotkey",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ConfigError(f"{field_name} must contain a hotkey combination.")

        if require_devices and not self.has_device_selection:
            raise ConfigError(
                "The configuration does not contain both selected audio devices. "
                "Run loud_gate.py --setup to select them."
            )


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / APP_NAME


def config_path() -> Path:
    return app_dir() / "config.ini"


def log_path() -> Path:
    return app_dir() / "loud_gate.log"


def ensure_app_dir() -> Path:
    path = app_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_config() -> LoudGateConfig:
    return LoudGateConfig()


def _section_value(
    section: configparser.SectionProxy | dict[str, str],
    key: str,
    default: str | None = None,
) -> str | None:
    if key in section:
        if isinstance(section, dict):
            return section.get(key, default)
        return section.get(key, raw=True)
    return default


def _parse_optional_int(raw: str | None, field_name: str) -> int | None:
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{field_name} must be an integer or blank.") from exc


def _parse_float(raw: str | None, field_name: str, default: float) -> float:
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{field_name} must be a number.") from exc


def _parse_text(raw: str | None, default: str | None) -> str | None:
    if raw is None:
        return default
    value = raw.strip()
    return value if value else None


def migrate_config(cfg: LoudGateConfig, source_version: int) -> LoudGateConfig:
    """Upgrade a known older schema while retaining values and filling new defaults."""

    if source_version == CONFIG_VERSION:
        return cfg
    if not 1 <= source_version < CONFIG_VERSION:
        raise ConfigError(f"Unsupported configuration version {source_version}.")

    # Earlier INI schemas used the same setting names for existing values. New
    # settings are already populated from LoudGateConfig defaults above.
    cfg.version = CONFIG_VERSION
    return cfg


def _parse_config(parser: configparser.ConfigParser, path: Path) -> tuple[LoudGateConfig, int]:
    if not parser.has_section(CONFIG_SECTION):
        raise ConfigError(f"Config section [{CONFIG_SECTION}] is missing from {path}.")

    data = parser[CONFIG_SECTION]
    hotkey_data = parser[HOTKEY_CONFIG_SECTION] if parser.has_section(HOTKEY_CONFIG_SECTION) else {}
    raw_version = _section_value(data, "version")
    if raw_version is None:
        raise ConfigError(f"Config version is missing from {path}.")
    try:
        source_version = int(raw_version)
    except ValueError as exc:
        raise ConfigError(f"Config version in {path} must be an integer.") from exc

    if source_version < 1:
        raise ConfigError(f"Unsupported configuration version {source_version} in {path}.")
    if source_version > CONFIG_VERSION:
        raise ConfigError(
            f"Configuration version {source_version} is newer than this Loud Gate version "
            f"({CONFIG_VERSION}). Update Loud Gate before using this config."
        )

    cfg = default_config()
    cfg.version = source_version

    cfg.input_device_index = _parse_optional_int(
        _section_value(data, "input_device_index"), "input_device_index"
    )
    cfg.output_device_index = _parse_optional_int(
        _section_value(data, "output_device_index"), "output_device_index"
    )
    cfg.input_device_name = _parse_text(_section_value(data, "input_device_name"), None)
    cfg.input_device_hostapi = _parse_text(_section_value(data, "input_device_hostapi"), None)
    cfg.output_device_name = _parse_text(_section_value(data, "output_device_name"), None)
    cfg.output_device_hostapi = _parse_text(_section_value(data, "output_device_hostapi"), None)
    cfg.threshold_db = _parse_float(_section_value(data, "threshold_db"), "threshold_db", cfg.threshold_db)
    cfg.release_ms = _parse_float(_section_value(data, "release_ms"), "release_ms", cfg.release_ms)
    cfg.lookahead_ms = _parse_float(_section_value(data, "lookahead_ms"), "lookahead_ms", cfg.lookahead_ms)

    def hotkey_value(key: str, default: str) -> str:
        raw = _section_value(hotkey_data, key)
        if raw is None:
            raw = _section_value(data, key)
        return _parse_text(raw, default) or default

    cfg.mute_hotkey = hotkey_value("mute_hotkey", cfg.mute_hotkey)
    cfg.stop_hotkey = hotkey_value("stop_hotkey", cfg.stop_hotkey)
    cfg.threshold_down_hotkey = hotkey_value("threshold_down_hotkey", cfg.threshold_down_hotkey)
    cfg.threshold_up_hotkey = hotkey_value("threshold_up_hotkey", cfg.threshold_up_hotkey)
    cfg.threshold_step_db = _parse_float(
        _section_value(hotkey_data, "threshold_step_db")
        or _section_value(data, "threshold_step_db"),
        "threshold_step_db",
        cfg.threshold_step_db,
    )

    cfg = migrate_config(cfg, source_version)
    cfg.validate()
    return cfg, source_version


def load_config() -> LoudGateConfig | None:
    path = config_path()
    if not path.exists():
        return None

    parser = configparser.ConfigParser(interpolation=None)
    try:
        with path.open("r", encoding="utf-8") as file:
            parser.read_file(file)
    except (OSError, configparser.Error) as exc:
        raise ConfigError(f"Failed to load config from {path}: {exc}") from exc

    cfg, source_version = _parse_config(parser, path)

    if source_version < CONFIG_VERSION and cfg.has_device_selection:
        try:
            save_config(cfg)
        except ConfigError as exc:
            raise ConfigError(
                f"Configuration version {source_version} was understood, but migration could not be saved: {exc}"
            ) from exc

    return cfg


def _config_values(cfg: LoudGateConfig) -> dict[str, str]:
    return {
        field.name: "" if getattr(cfg, field.name) is None else str(getattr(cfg, field.name))
        for field in fields(cfg)
    }


def save_config(cfg: LoudGateConfig) -> None:
    cfg.validate(require_devices=True)
    ensure_app_dir()
    path = config_path()
    tmp = path.with_suffix(".tmp")
    values = _config_values(cfg)

    parser = configparser.ConfigParser(interpolation=None)
    parser[CONFIG_SECTION] = {
        key: value
        for key, value in sorted(values.items())
        if not (key.endswith("_hotkey") or key == "threshold_step_db")
    }
    parser[HOTKEY_CONFIG_SECTION] = {
        key: value
        for key, value in sorted(values.items())
        if key.endswith("_hotkey") or key == "threshold_step_db"
    }

    try:
        with tmp.open("w", encoding="utf-8", newline="") as file:
            parser.write(file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise ConfigError(f"Failed to save config to {path}: {exc}") from exc
