import os
import threading
from pathlib import Path
from typing import Dict, Set


def _normalize_uuid(value: str) -> str:
    """规范化 UUID: 去除连字符并转小写, 便于比较。"""
    return value.replace("-", "").strip().lower()


class Whitelist:
    """
    按认证源 (entry) 配置的 UUID 白名单, 支持热读取。

    文件格式 (INI 风格)::

        [union]
        4845a2d91444325caa4e772f16e04762
        588a5182b704380b87cf295b0d1e39c6

        [mojang]
        a94c19ea783b41eb87921e5f256be9dd

    - 每个段头 ``[entry_id]`` 对应 ``config.toml`` 中的一个入口 id。
    - 段下每行一个 UUID, 可带或不带连字符, 大小写不敏感。
    - 支持注释: ``#`` 之后的全部内容视为注释, 可写在行首或行中, 空行会被忽略。
    - 若某 entry 没有对应段, 则该 entry 不做白名单限制 (放行所有)。
    - 若某 entry 有对应段但为空, 则该 entry 拒绝所有人。

    热读取: 每次 ``is_allowed`` 调用时检查文件签名 (mtime, size),
    仅在文件变化时重新解析, 无需重启服务。
    """

    def __init__(self, filepath):
        self.filepath = Path(filepath)
        self._lock = threading.Lock()
        self._entries: Dict[str, Set[str]] = {}
        self._loaded_sig = None  # (mtime_ns, size), 上次加载时的文件签名
        self._load_unlocked()

    def _file_sig_unlocked(self):
        """返回文件签名 (mtime_ns, size), 文件不存在则返回 None。"""
        try:
            st = os.stat(self.filepath)
        except FileNotFoundError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def _load_unlocked(self):
        """解析白名单文件, 构建内存索引。调用方持锁。"""
        entries: Dict[str, Set[str]] = {}
        sig = self._file_sig_unlocked()
        if sig is not None:
            current_section = None
            with open(self.filepath, "r", encoding="utf-8") as f:
                for raw_line in f:
                    # 行内注释: '#' 之后的内容全部忽略, 再去除首尾空白。
                    line = raw_line.split("#", 1)[0].strip()
                    if not line:
                        continue
                    if line.startswith("[") and line.endswith("]"):
                        current_section = line[1:-1].strip()
                        if current_section:
                            entries.setdefault(current_section, set())
                        else:
                            current_section = None
                        continue
                    if current_section is None:
                        continue
                    normalized = _normalize_uuid(line)
                    if normalized:
                        entries[current_section].add(normalized)
        self._entries = entries
        self._loaded_sig = sig

    def _maybe_reload_unlocked(self):
        """若文件签名变化则重新加载, 否则跳过。调用方持锁。"""
        sig = self._file_sig_unlocked()
        if sig == self._loaded_sig:
            return
        self._load_unlocked()

    def is_allowed(self, entry_id: str, uuid_value: str) -> bool:
        """
        检查某 entry 的 UUID 是否在白名单中。

        - entry 无白名单段: 放行 (返回 True)。
        - entry 有白名单段: 仅当 UUID 在集合中才放行。
        """
        normalized = _normalize_uuid(uuid_value)
        with self._lock:
            self._maybe_reload_unlocked()
            allowed_set = self._entries.get(entry_id)
            if allowed_set is None:
                return True
            return normalized in allowed_set

    def configured_entries(self):
        """返回已配置白名单段的 entry id 集合。"""
        with self._lock:
            self._maybe_reload_unlocked()
            return set(self._entries.keys())
