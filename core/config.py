from __future__ import annotations

import configparser
from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.ini"
USER_CONFIG_PATH = REPO_ROOT / "config.user.ini"


def config_path() -> Path:
    """Return the committed default config path."""
    return DEFAULT_CONFIG_PATH


def config_paths() -> list[Path]:
    """Return config files in load order: defaults, then optional user override."""
    paths = [DEFAULT_CONFIG_PATH]
    if USER_CONFIG_PATH.exists():
        paths.append(USER_CONFIG_PATH)
    return paths


def read_config(*, raw: bool = False, preserve_case: bool = False) -> configparser.ConfigParser:
    parser_cls = configparser.RawConfigParser if raw else configparser.ConfigParser
    cfg = parser_cls()
    if preserve_case:
        cfg.optionxform = str
    cfg.read([str(p) for p in config_paths()])
    return cfg
