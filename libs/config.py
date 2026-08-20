import os
from pathlib import Path
from string import Formatter
from typing import Dict, Union

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"
MAX_PROFILE_NAME_LENGTH = 16


def load_config(filepath: Union[str, os.PathLike] = DEFAULT_CONFIG_PATH) -> dict:
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Config file '{filepath}' not found")

    with open(filepath, "rb") as f:
        config = tomllib.load(f)

    if not isinstance(config, dict):
        raise ValueError("Config root must be a table")
    return config


def _validate_always_format(config: dict) -> bool:
    always_format = config.get("alwaysFormat", False)
    if not isinstance(always_format, bool):
        raise ValueError("Config 'alwaysFormat' must be a boolean")
    return always_format


def _validate_key(config: dict) -> str:
    key = config.get("key", "")
    if not isinstance(key, str):
        raise ValueError("Config 'key' must be a string")
    return key


def _validate_token_expires_in(config: dict) -> int:
    token_expires_in = config.get("tokenExpiresIn", 600)
    if isinstance(token_expires_in, bool) or not isinstance(token_expires_in, int) or token_expires_in <= 0:
        raise ValueError("Config 'tokenExpiresIn' must be a positive integer")
    return token_expires_in


def _validate_entries(config: dict) -> Dict[str, Dict[str, str]]:
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
            field_names = [
                field_name.split(".", 1)[0].split("[", 1)[0]
                for _, field_name, _, _ in Formatter().parse(name_format)
                if field_name is not None
            ]
        except ValueError as e:
            raise ValueError(f"Entry '{entry_id}' format is invalid: {e}") from e
        invalid_fields = sorted(set(field_names) - {"name", "entry"})
        if invalid_fields:
            raise ValueError(
                f"Entry '{entry_id}' format contains unsupported field(s): {', '.join(invalid_fields)}"
            )
        has_name_placeholder = "name" in field_names
        if not has_name_placeholder:
            raise ValueError(f"Entry '{entry_id}' format must contain '{{name}}'")

        # 校验 format 能否生成合法长度的 Minecraft 玩家名 (≤16 字符)
        shortest_formatted = name_format.format(name="a", entry=entry_id)
        if len(shortest_formatted) > MAX_PROFILE_NAME_LENGTH:
            raise ValueError(
                f"Entry '{entry_id}' format '{name_format}' produces "
                f"'{shortest_formatted}' ({len(shortest_formatted)} chars) even with a "
                f"single-char name, exceeding the {MAX_PROFILE_NAME_LENGTH}-char limit"
            )

        result[entry_id] = {
            "api": api,
            "format": name_format,
        }

    return result


def load_all(filepath: Union[str, os.PathLike] = DEFAULT_CONFIG_PATH) -> dict:
    """一次性加载并校验全部配置, 避免多次打开/解析同一 TOML 文件。"""
    config = load_config(filepath)
    return {
        "always_format": _validate_always_format(config),
        "key": _validate_key(config),
        "token_expires_in": _validate_token_expires_in(config),
        "entries": _validate_entries(config),
    }


def load_always_format(filepath: Union[str, os.PathLike] = DEFAULT_CONFIG_PATH) -> bool:
    return _validate_always_format(load_config(filepath))


def load_key(filepath: Union[str, os.PathLike] = DEFAULT_CONFIG_PATH) -> str:
    return _validate_key(load_config(filepath))


def load_token_expires_in(filepath: Union[str, os.PathLike] = DEFAULT_CONFIG_PATH) -> int:
    return _validate_token_expires_in(load_config(filepath))


def load_entries(filepath: Union[str, os.PathLike] = DEFAULT_CONFIG_PATH) -> Dict[str, Dict[str, str]]:
    return _validate_entries(load_config(filepath))
