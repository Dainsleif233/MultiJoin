import os
from pathlib import Path
from typing import Dict, Union

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"


def load_config(filepath: Union[str, os.PathLike] = DEFAULT_CONFIG_PATH) -> dict:
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Config file '{filepath}' not found")

    with open(filepath, "rb") as f:
        config = tomllib.load(f)

    if not isinstance(config, dict):
        raise ValueError("Config root must be a table")
    return config


def load_entries(filepath: Union[str, os.PathLike] = DEFAULT_CONFIG_PATH) -> Dict[str, Dict[str, str]]:
    config = load_config(filepath)
    entries = config.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Config must contain at least one [[entries]] table")

    result = {}
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"Entry #{index} must be a table")

        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not entry_id:
            raise ValueError(f"Entry #{index} must define a non-empty id")
        if entry_id in result:
            raise ValueError(f"Entry id '{entry_id}' is duplicated")

        api = entry.get("api")
        name_format = entry.get("format")
        if not isinstance(api, str) or not api:
            raise ValueError(f"Entry '{entry_id}' must define a non-empty api")
        if not isinstance(name_format, str) or not name_format:
            raise ValueError(f"Entry '{entry_id}' must define a non-empty format")

        try:
            name_format.format(name="name", entry_id=entry_id)
        except (IndexError, KeyError, ValueError) as e:
            raise ValueError(f"Entry '{entry_id}' format is invalid: {e}") from e

        result[entry_id] = {
            "api": api,
            "format": name_format,
        }

    return result
