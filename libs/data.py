import csv
import os
import sys
import tempfile
import threading
from collections import Counter
from contextlib import contextmanager
from pathlib import Path


class ProfilesData:
    """
    极轻量的 profile 映射表, 持久化为 CSV 文件。

    CSV 列含义:
    - Profile: 最终返回给代理端的 UUID
    - Entry: 来源入口 ID
    - UUID: 入口返回的原始 UUID
    - Name: 最终返回给代理端的玩家名
    - Bind: 绑定到的 Profile
    """

    HEADER = ["Profile", "Entry", "UUID", "Name", "Bind"]
    _locks_guard = threading.Lock()
    _locks = {}

    def __init__(self, filepath: str):
        """
        初始化数据表, 若文件存在则加载, 否则创建空表。
        :param filepath: CSV 文件路径
        """
        self.filepath = Path(filepath)
        self._lock = self._get_lock(self.filepath)
        self.profile_to_record = {}  # Profile -> (Entry, UUID, Name, Bind)
        self.entry_uuid_to_profile = {}  # (Entry, UUID) -> Profile
        self.uuid_counter = Counter()  # UUID -> 出现次数, 用于快速检查 UUID 是否已存在
        self.name_to_profiles = {}  # Name -> set(Profile), 用于快速检查玩家名冲突
        self.bound_profiles = set()  # set(Profile), 用于快速检查 Profile 是否已有 Bind
        self._latest_depth = 0
        self._loaded_sig = None  # (mtime_ns, size), 上次加载时的文件签名, 用于惰性重载
        self._load()

    @classmethod
    def _get_lock(cls, filepath: Path):
        resolved_path = filepath.resolve()
        with cls._locks_guard:
            lock = cls._locks.get(resolved_path)
            if lock is None:
                lock = threading.RLock()
                cls._locks[resolved_path] = lock
            return lock

    def _clear_indexes(self):
        self.profile_to_record = {}
        self.entry_uuid_to_profile = {}
        self.uuid_counter = Counter()
        self.name_to_profiles = {}
        self.bound_profiles = set()

    def _index_record(self, profile: str, entry: str, original_uuid: str, name: str, bind: str):
        """为一条记录建立所有查询索引。"""
        self.entry_uuid_to_profile[(entry, original_uuid)] = profile
        self.uuid_counter[original_uuid] += 1
        self.name_to_profiles.setdefault(name, set()).add(profile)
        if bind:
            self.bound_profiles.add(profile)

    def _remove_name_index(self, profile: str, name: str):
        profiles = self.name_to_profiles.get(name)
        if profiles is None:
            return

        profiles.discard(profile)
        if not profiles:
            del self.name_to_profiles[name]

    def _file_sig_unlocked(self):
        """返回文件签名 (mtime_ns, size), 文件不存在则返回 None。"""
        try:
            st = os.stat(self.filepath)
        except FileNotFoundError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def _load_unlocked(self):
        """从 CSV 文件加载数据, 构建内存索引。"""
        self._clear_indexes()
        sig = self._file_sig_unlocked()
        if sig is None:
            self._loaded_sig = None
            return

        with open(self.filepath, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            try:
                header = next(reader)
            except StopIteration:
                self._loaded_sig = sig
                return
            if header != self.HEADER:
                raise ValueError(f"Profiles CSV header must be: {', '.join(self.HEADER)}")

            for line_number, row in enumerate(reader, start=2):
                if len(row) != len(self.HEADER):
                    raise ValueError(f"Profiles CSV line {line_number} must contain {len(self.HEADER)} columns")

                profile, entry, original_uuid, name, bind = row

                self.profile_to_record[profile] = (entry, original_uuid, name, bind)
                self._index_record(profile, entry, original_uuid, name, bind)
        self._loaded_sig = sig

    def _maybe_reload_unlocked(self):
        """若文件签名变化则重载, 否则跳过。用于惰性重载, 避免每次查询全量读盘。"""
        sig = self._file_sig_unlocked()
        if sig == self._loaded_sig:
            return
        self._load_unlocked()

    def _load(self):
        """从最新 CSV 文件加载数据, 构建内存索引。"""
        with self._lock:
            self._load_unlocked()

    def _refresh_unlocked(self):
        if self._latest_depth == 0:
            self._maybe_reload_unlocked()

    def _save_unlocked(self):
        """将当前数据写回 CSV 文件, 按 Profile 排序保证可读性。"""
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        with tempfile.NamedTemporaryFile(
            "w",
            newline="",
            encoding="utf-8",
            dir=self.filepath.parent,
            delete=False,
        ) as f:
            temp_path = Path(f.name)
            writer = csv.writer(f)
            writer.writerow(self.HEADER)
            for profile, (entry, original_uuid, name, bind) in sorted(self.profile_to_record.items()):
                writer.writerow([profile, entry, original_uuid, name, bind])
            f.flush()
            os.fsync(f.fileno())
        try:
            os.replace(temp_path, self.filepath)
        except OSError:
            print(
                f"[DATA] Failed to persist profiles to {self.filepath}, "
                f"data saved to temporary file {temp_path}. "
                f"Error: {sys.exc_info()[1]}",
            )
            # 写盘失败, 从磁盘重载以回滚已修改的内存索引, 避免内存-磁盘永久偏离。
            self._load_unlocked()
            raise
        else:
            temp_path = None
            # 写者刚落盘, 主动更新签名, 避免下一次查询又触发一次重载。
            self._loaded_sig = self._file_sig_unlocked()
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()

    @contextmanager
    def latest(self):
        """锁定当前 CSV, 并在锁内加载最新文件供一组读写操作使用。"""
        with self._lock:
            self._maybe_reload_unlocked()
            self._latest_depth += 1
            try:
                yield self
            finally:
                self._latest_depth -= 1

    def query_profile_by_entry_uuid(self, entry: str, original_uuid: str):
        """
        通过 Entry 和 UUID 查询对应的 Profile。
        :return: Profile, 若 (Entry, UUID) 不存在则返回 None
        """
        with self._lock:
            self._refresh_unlocked()
            return self.entry_uuid_to_profile.get((entry, original_uuid))

    def exists_uuid(self, original_uuid: str) -> bool:
        """
        检查某个 UUID 是否已存在。
        :return: True/False
        """
        with self._lock:
            self._refresh_unlocked()
            return self.uuid_counter[original_uuid] > 0

    def exists_profile(self, profile: str) -> bool:
        """
        检查某个 Profile 是否存在。
        :return: True/False
        """
        with self._lock:
            self._refresh_unlocked()
            return profile in self.profile_to_record

    def exists_name(self, name: str) -> bool:
        """检查是否存在 Name 为 name 的记录。"""
        with self._lock:
            self._refresh_unlocked()
            return name in self.name_to_profiles

    def exists_name_except_profile(self, profile: str, name: str) -> bool:
        """
        检查是否存在 Name 为 name 且 Profile 不等于 profile 的记录。
        :return: True/False
        """
        with self._lock:
            self._refresh_unlocked()
            return any(current_profile != profile for current_profile in self.name_to_profiles.get(name, ()))

    def get_bind_by_profile(self, profile: str) -> str:
        """通过 Profile 读取 Bind。"""
        with self._lock:
            self._refresh_unlocked()
            if profile not in self.profile_to_record:
                raise KeyError(f"Profile '{profile}' 不存在")

            _, _, _, bind = self.profile_to_record[profile]
            return bind

    def is_unbound_profile(self, profile: str) -> bool:
        """检查 Profile 存在且 Bind 为空。"""
        with self._lock:
            self._refresh_unlocked()
            return profile in self.profile_to_record and profile not in self.bound_profiles

    def add(self, profile: str, entry: str, original_uuid: str, name: str = ""):
        """添加一条记录, 委托给 add_with_name。"""
        self.add_with_name(profile, entry, original_uuid, name)

    def add_with_name(self, profile: str, entry: str, original_uuid: str, name: str):
        """
        新建一条记录并直接以最终 name 入库, 一次落盘。
        必须保证:
        - Profile 尚未存在
        - (Entry, UUID) 组合尚未存在
        用于合并 handleProfile 中 "add + update_name" 两次写为一次。
        """
        with self._lock:
            self._refresh_unlocked()
            if profile in self.profile_to_record:
                raise ValueError(f"Profile '{profile}' 已存在")
            if (entry, original_uuid) in self.entry_uuid_to_profile:
                raise ValueError(f"(Entry, UUID) 组合 ('{entry}', '{original_uuid}') 已存在")

            self.profile_to_record[profile] = (entry, original_uuid, name, "")
            self._index_record(profile, entry, original_uuid, name, "")
            self._save_unlocked()

    def update_name_by_profile(self, profile: str, new_name: str):
        """通过 Profile 更新对应的 Name。"""
        with self._lock:
            self._refresh_unlocked()
            if profile not in self.profile_to_record:
                raise KeyError(f"Profile '{profile}' 不存在")

            entry, original_uuid, old_name, bind = self.profile_to_record[profile]
            if new_name == old_name:
                return

            self._remove_name_index(profile, old_name)
            self.profile_to_record[profile] = (entry, original_uuid, new_name, bind)
            self.name_to_profiles.setdefault(new_name, set()).add(profile)
            self._save_unlocked()

    def update_bind_by_profile(self, profile: str, bind: str):
        """通过 Profile 更新对应的 Bind。"""
        with self._lock:
            self._refresh_unlocked()
            if profile not in self.profile_to_record:
                raise KeyError(f"Profile '{profile}' 不存在")

            entry, original_uuid, name, old_bind = self.profile_to_record[profile]
            if bind == old_bind:
                return

            self.profile_to_record[profile] = (entry, original_uuid, name, bind)
            if bind:
                self.bound_profiles.add(profile)
            else:
                self.bound_profiles.discard(profile)
            self._save_unlocked()

    def clear_bind_by_profile(self, profile: str):
        """通过 Profile 清空对应的 Bind。"""
        self.update_bind_by_profile(profile, "")
