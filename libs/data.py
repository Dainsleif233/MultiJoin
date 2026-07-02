import csv
import json
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
        self.wal_path = self.filepath.with_suffix(".wal")
        self._lock = self._get_lock(self.filepath)
        self.profile_to_record = {}  # Profile -> (Entry, UUID, Name, Bind)
        self.entry_uuid_to_profile = {}  # (Entry, UUID) -> Profile
        self.uuid_counter = Counter()  # UUID -> 出现次数, 用于快速检查 UUID 是否已存在
        self.name_to_profiles = {}  # Name -> set(Profile), 用于快速检查玩家名冲突
        self.bound_profiles = set()  # set(Profile), 用于快速检查 Profile 是否已有 Bind
        self._latest_depth = 0
        self._loaded_sig = None  # (mtime_ns, size), 上次加载时的文件签名, 用于惰性重载
        # WAL: 写操作先追加到此文件 (append + fsync), 后台线程定时 checkpoint 合并回 CSV。
        self._dirty = False  # 是否有未 checkpoint 的 WAL 操作, 内存比 CSV 新
        self._dirty_count = 0  # 自上次 checkpoint 以来的脏操作数, 触发阈值则提前 checkpoint
        self._flush_interval = 5.0  # 后台 checkpoint 周期 (秒)
        self._flush_threshold = 256  # 脏操作阈值, 达到则后台线程立即 checkpoint
        self._stopping = threading.Event()
        self._flush_thread = None
        self._wal_fp = None  # 复用的 WAL 追加句柄, 延迟到 _open_wal_unlocked 打开
        self._load()
        self._start_flush_thread()

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

    def _set_record_unlocked(self, profile: str, entry: str, original_uuid: str, name: str, bind: str):
        """
        直接以最终值覆盖一条记录的全部字段并维护索引, 不做任何约束检查。
        - 正常写路径: 先检查约束, 再调本方法落内存 + 追加 WAL。
        - WAL 重放路径: 直接调本方法, 跳过约束 (崩溃恢复时数据已通过校验)。
        幂等: 对同一 profile 重复设置最终值, 结果一致。
        """
        old = self.profile_to_record.get(profile)
        if old is not None:
            old_entry, old_uuid, old_name, old_bind = old
            # 完全拆除旧记录的所有索引, 再统一重建新索引, 保证计数不漂移。
            if self.entry_uuid_to_profile.get((old_entry, old_uuid)) == profile:
                del self.entry_uuid_to_profile[(old_entry, old_uuid)]
            self.uuid_counter[old_uuid] -= 1
            self._remove_name_index(profile, old_name)
            if old_bind:
                self.bound_profiles.discard(profile)
        self.profile_to_record[profile] = (entry, original_uuid, name, bind)
        self._index_record(profile, entry, original_uuid, name, bind)

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
        """从 CSV 文件加载数据, 构建内存索引, 然后重放 WAL 并 checkpoint 固化。"""
        self._clear_indexes()
        sig = self._file_sig_unlocked()
        if sig is not None:
            with open(self.filepath, "r", newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                try:
                    header = next(reader)
                except StopIteration:
                    header = None
                if header is not None:
                    if header != self.HEADER:
                        raise ValueError(f"Profiles CSV header must be: {', '.join(self.HEADER)}")
                    for line_number, row in enumerate(reader, start=2):
                        if len(row) != len(self.HEADER):
                            raise ValueError(f"Profiles CSV line {line_number} must contain {len(self.HEADER)} columns")
                        profile, entry, original_uuid, name, bind = row
                        self.profile_to_record[profile] = (entry, original_uuid, name, bind)
                        self._index_record(profile, entry, original_uuid, name, bind)
        self._loaded_sig = sig
        # 无论 CSV 是否存在, 都要重放 WAL (崩溃时可能只有 WAL), 然后立即 checkpoint 固化。
        self._replay_wal_unlocked()
        self._checkpoint_unlocked()

    def _maybe_reload_unlocked(self):
        """若文件签名变化则重载, 否则跳过。用于惰性重载, 避免每次查询全量读盘。"""
        if self._dirty:
            # 有未 checkpoint 的 WAL 操作时, 内存是权威源, 不能从磁盘重载 (会丢 WAL)。
            return
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
            # 不回滚内存: WAL 模式下内存是权威源, 回滚会丢失已确认的 WAL 写。
            # 保留 dirty, 下次 checkpoint 重试; CSV 落后但完整。
            raise
        else:
            temp_path = None
            # 写者刚落盘, 主动更新签名, 避免下一次查询又触发一次重载。
            self._loaded_sig = self._file_sig_unlocked()
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()

    def _open_wal_unlocked(self):
        """打开 (或复用) WAL 追加句柄。调用方持锁。"""
        if self._wal_fp is not None and not self._wal_fp.closed:
            return self._wal_fp
        # "a" 模式: 文件不存在则创建, 写入追加到末尾, 不截断。
        self._wal_fp = open(self.wal_path, "a", encoding="utf-8")
        return self._wal_fp

    def _append_wal_unlocked(self, record: dict):
        """追加一条操作记录到 WAL 并 fsync, 保证崩溃不丢已确认的写。调用方持锁。"""
        fp = self._open_wal_unlocked()
        fp.write(json.dumps(record, ensure_ascii=False) + "\n")
        fp.flush()
        os.fsync(fp.fileno())
        self._dirty = True
        self._dirty_count += 1
        # 脏操作累积到阈值则立即 checkpoint, 防止 WAL 无限增长 (同步, 在锁内)。
        if self._dirty_count >= self._flush_threshold:
            self._checkpoint_unlocked()

    def _replay_wal_unlocked(self):
        """启动恢复: 顺序重放 WAL 到内存索引, 跳过约束检查。调用方持锁。"""
        try:
            f = open(self.wal_path, "r", encoding="utf-8")
        except FileNotFoundError:
            return
        with f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    # 崩溃时可能写入半行, 跳过 (该写未向客户端确认成功)。
                    continue
                op = record.get("op")
                if op == "add":
                    self._set_record_unlocked(
                        record["profile"], record["entry"], record["uuid"],
                        record["name"], "",
                    )
                elif op == "update_name":
                    self._set_record_unlocked(
                        record["profile"], record["entry"], record["uuid"],
                        record["name"], record["bind"],
                    )
                elif op == "update_bind":
                    self._set_record_unlocked(
                        record["profile"], record["entry"], record["uuid"],
                        record["name"], record["bind"],
                    )
        # 重放已并入内存, 由紧跟的 checkpoint 固化回 CSV 并清空 WAL。
        self._dirty = True

    def _truncate_wal_unlocked(self):
        """清空 WAL (checkpoint 成功后调用)。调用方持锁。"""
        fp = self._open_wal_unlocked()
        fp.seek(0)
        fp.truncate(0)
        fp.flush()
        os.fsync(fp.fileno())

    def _checkpoint_unlocked(self):
        """
        把内存全量快照写回 CSV, 成功后清空 WAL。调用方持锁。
        顺序: 写 CSV 临时文件 -> fsync -> os.replace -> 更新签名 -> 清空 WAL。
        清空 WAL 必须在 CSV 落盘成功之后, 保证崩溃时 WAL 重放总能得到正确状态。
        """
        if not self._dirty:
            return
        try:
            self._save_unlocked()
        except OSError:
            # CSV 落盘失败: 保留 dirty 与 WAL, 下次重试。内存仍是权威源。
            return
        self._truncate_wal_unlocked()
        self._dirty = False
        self._dirty_count = 0

    def _flush_loop(self):
        """后台线程: 周期性 checkpoint, 退出前最后落盘一次。"""
        while True:
            if self._stopping.wait(self._flush_interval):
                break
            try:
                with self._lock:
                    self._checkpoint_unlocked()
            except Exception as e:  # noqa: BLE001 - 后台线程不能因单次失败退出
                print(f"[DATA] background checkpoint failed: {e}")
        # 退出前最后落盘
        try:
            with self._lock:
                self._checkpoint_unlocked()
        except Exception as e:  # noqa: BLE001
            print(f"[DATA] final checkpoint failed: {e}")

    def _start_flush_thread(self):
        self._flush_thread = threading.Thread(
            target=self._flush_loop, name="profiles-checkpoint", daemon=True,
        )
        self._flush_thread.start()

    def close(self):
        """优雅关闭: 停后台线程并做最后一次 checkpoint, 然后关闭 WAL 句柄。"""
        self._stopping.set()
        if self._flush_thread is not None:
            self._flush_thread.join(timeout=30)
        with self._lock:
            self._checkpoint_unlocked()
            if self._wal_fp is not None and not self._wal_fp.closed:
                self._wal_fp.close()
                self._wal_fp = None

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

            self._set_record_unlocked(profile, entry, original_uuid, name, "")
            self._append_wal_unlocked({
                "op": "add",
                "profile": profile,
                "entry": entry,
                "uuid": original_uuid,
                "name": name,
            })

    def update_name_by_profile(self, profile: str, new_name: str):
        """通过 Profile 更新对应的 Name。"""
        with self._lock:
            self._refresh_unlocked()
            if profile not in self.profile_to_record:
                raise KeyError(f"Profile '{profile}' 不存在")

            entry, original_uuid, old_name, bind = self.profile_to_record[profile]
            if new_name == old_name:
                return

            self._set_record_unlocked(profile, entry, original_uuid, new_name, bind)
            self._append_wal_unlocked({
                "op": "update_name",
                "profile": profile,
                "entry": entry,
                "uuid": original_uuid,
                "name": new_name,
                "bind": bind,
            })

    def update_bind_by_profile(self, profile: str, bind: str):
        """通过 Profile 更新对应的 Bind。"""
        with self._lock:
            self._refresh_unlocked()
            if profile not in self.profile_to_record:
                raise KeyError(f"Profile '{profile}' 不存在")

            entry, original_uuid, name, old_bind = self.profile_to_record[profile]
            if bind == old_bind:
                return

            self._set_record_unlocked(profile, entry, original_uuid, name, bind)
            self._append_wal_unlocked({
                "op": "update_bind",
                "profile": profile,
                "entry": entry,
                "uuid": original_uuid,
                "name": name,
                "bind": bind,
            })

    def clear_bind_by_profile(self, profile: str):
        """通过 Profile 清空对应的 Bind。"""
        self.update_bind_by_profile(profile, "")
