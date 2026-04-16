import csv
import os
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
    """

    HEADER = ["Profile", "Entry", "UUID", "Name"]
    _locks_guard = threading.Lock()
    _locks = {}

    def __init__(self, filepath: str):
        """
        初始化数据表, 若文件存在则加载, 否则创建空表。
        :param filepath: CSV 文件路径
        """
        self.filepath = Path(filepath)
        self._lock = self._get_lock(self.filepath)
        self.profile_to_record = {}  # Profile -> (Entry, UUID, Name)
        self.entry_uuid_to_profile = {}  # (Entry, UUID) -> Profile
        self.uuid_counter = Counter()  # UUID -> 出现次数, 用于快速检查 UUID 是否已存在
        self.name_to_profiles = {}  # Name -> set(Profile), 用于快速检查玩家名冲突
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

    def _index_record(self, profile: str, entry: str, original_uuid: str, name: str):
        """为一条记录建立所有查询索引。"""
        self.entry_uuid_to_profile[(entry, original_uuid)] = profile
        self.uuid_counter[original_uuid] += 1
        self.name_to_profiles.setdefault(name, set()).add(profile)

    def _remove_name_index(self, profile: str, name: str):
        profiles = self.name_to_profiles.get(name)
        if profiles is None:
            return

        profiles.discard(profile)
        if not profiles:
            del self.name_to_profiles[name]

    def _load_unlocked(self):
        """从 CSV 文件加载数据, 构建内存索引。"""
        self._clear_indexes()
        if not os.path.exists(self.filepath):
            return

        with open(self.filepath, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            try:
                next(reader)  # 跳过标题行
            except StopIteration:
                return

            for row in reader:
                if len(row) not in (3, 4):
                    continue

                if len(row) == 3:
                    profile, entry, original_uuid = row
                    name = ""
                else:
                    profile, entry, original_uuid, name = row

                self.profile_to_record[profile] = (entry, original_uuid, name)
                self._index_record(profile, entry, original_uuid, name)

    def _load(self):
        """从最新 CSV 文件加载数据, 构建内存索引。"""
        with self._lock:
            self._load_unlocked()

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
            for profile, (entry, original_uuid, name) in sorted(self.profile_to_record.items()):
                writer.writerow([profile, entry, original_uuid, name])
            f.flush()
            os.fsync(f.fileno())
        try:
            os.replace(temp_path, self.filepath)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()

    @contextmanager
    def latest(self):
        """锁定当前 CSV, 并在锁内加载最新文件供一组读写操作使用。"""
        with self._lock:
            self._load_unlocked()
            yield self

    def query_profile_by_entry_uuid(self, entry: str, original_uuid: str):
        """
        通过 Entry 和 UUID 查询对应的 Profile。
        :return: Profile, 若 (Entry, UUID) 不存在则返回 None
        """
        with self._lock:
            self._load_unlocked()
            return self.entry_uuid_to_profile.get((entry, original_uuid))

    def exists_uuid(self, original_uuid: str) -> bool:
        """
        检查某个 UUID 是否已存在。
        :return: True/False
        """
        with self._lock:
            self._load_unlocked()
            return self.uuid_counter[original_uuid] > 0

    def exists_name_except_profile(self, profile: str, name: str) -> bool:
        """
        检查是否存在 Name 为 name 且 Profile 不等于 profile 的记录。
        :return: True/False
        """
        with self._lock:
            self._load_unlocked()
            return any(current_profile != profile for current_profile in self.name_to_profiles.get(name, ()))

    def add(self, profile: str, entry: str, original_uuid: str, name: str = ""):
        """
        添加一条记录, 必须保证:
        - Profile 尚未存在
        - (Entry, UUID) 组合尚未存在
        """
        with self._lock:
            self._load_unlocked()
            if profile in self.profile_to_record:
                raise ValueError(f"Profile '{profile}' 已存在")
            if (entry, original_uuid) in self.entry_uuid_to_profile:
                raise ValueError(f"(Entry, UUID) 组合 ('{entry}', '{original_uuid}') 已存在")

            self.profile_to_record[profile] = (entry, original_uuid, name)
            self._index_record(profile, entry, original_uuid, name)
            self._save_unlocked()

    def update_name_by_profile(self, profile: str, new_name: str):
        """通过 Profile 更新对应的 Name。"""
        with self._lock:
            self._load_unlocked()
            if profile not in self.profile_to_record:
                raise KeyError(f"Profile '{profile}' 不存在")

            entry, original_uuid, old_name = self.profile_to_record[profile]
            if new_name == old_name:
                return

            self._remove_name_index(profile, old_name)
            self.profile_to_record[profile] = (entry, original_uuid, new_name)
            self.name_to_profiles.setdefault(new_name, set()).add(profile)
            self._save_unlocked()
