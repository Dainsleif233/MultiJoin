import csv
import os
from collections import Counter

class LightweightDataTable:
    """
    极轻量数据存取类，模拟四列表格：
    - A列唯一，对应唯一的(B, C, D)组合
    - 支持通过A查询(B, C)与D、通过(B, C)查询A、通过C查询是否存在
    - 数据持久化为可读的CSV文件
    """

    def __init__(self, filepath: str):
        """
        初始化数据表，若文件存在则加载，否则创建空表。
        :param filepath: CSV文件路径（推荐使用 .csv 后缀）
        """
        self.filepath = filepath
        # 主索引
        self.a_to_bc = {}       # A -> (B, C, D)
        self.bc_to_a = {}       # (B, C) -> A
        self.c_counter = Counter()  # C -> 出现次数，用于快速存在性检查
        self._load()

    def _load(self):
        """从CSV文件加载数据，构建内存索引"""
        if not os.path.exists(self.filepath):
            return  # 新文件，无数据

        with open(self.filepath, 'r', newline='', encoding='utf-8') as f:
            # 使用csv阅读器，支持字段内逗号/引号
            reader = csv.reader(f)
            try:
                header = next(reader)  # 跳过标题行
            except StopIteration:
                return  # 空文件

            for row in reader:
                if len(row) not in (3, 4):
                    continue  # 忽略格式错误行
                if len(row) == 3:
                    a, b, c = row
                    d = ""
                else:
                    a, b, c, d = row
                # 构建索引
                self.a_to_bc[a] = (b, c, d)
                self.bc_to_a[(b, c)] = a
                self.c_counter[c] += 1

    def _save(self):
        """将当前数据写回CSV文件（按A排序，保证可读性）"""
        with open(self.filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['A', 'B', 'C', 'D'])
            # 按A排序写入，提升可读性
            for a, (b, c, d) in sorted(self.a_to_bc.items()):
                writer.writerow([a, b, c, d])

    # -------------------- 核心查询接口 --------------------
    def query_by_a(self, a: str):
        """
        通过A查询对应的(B, C)
        :return: (B, C) 元组，若A不存在则返回None
        """
        item = self.a_to_bc.get(a)
        if item is None:
            return None
        b, c, _ = item
        return (b, c)

    def query_d_by_a(self, a: str):
        """
        通过A查询对应的D
        :return: D值，若A不存在则返回None
        """
        item = self.a_to_bc.get(a)
        if item is None:
            return None
        return item[2]

    def query_by_bc(self, b: str, c: str):
        """
        通过B和C组合查询对应的A
        :return: A值，若(B, C)不存在则返回None
        """
        return self.bc_to_a.get((b, c))

    def exists_c(self, c: str) -> bool:
        """
        检查某个C值是否存在（至少一条记录）
        :return: True/False
        """
        return self.c_counter[c] > 0

    def exists_d(self, a: str, d: str) -> bool:
        """
        检查是否存在 D 值为 d 且 A 不等于 a 的记录
        :return: True/False
        """
        for current_a, (_, _, current_d) in self.a_to_bc.items():
            if current_a != a and current_d == d:
                return True
        return False

    # -------------------- 数据修改接口（自动持久化） --------------------
    def add(self, a: str, b: str, c: str, d: str = ""):
        """
        添加一条记录，必须保证：
        - A 尚未存在
        - (B, C) 组合尚未存在
        """
        if a in self.a_to_bc:
            raise ValueError(f"A 值 '{a}' 已存在")
        if (b, c) in self.bc_to_a:
            raise ValueError(f"(B, C) 组合 ('{b}', '{c}') 已存在")

        # 添加索引
        self.a_to_bc[a] = (b, c, d)
        self.bc_to_a[(b, c)] = a
        self.c_counter[c] += 1
        self._save()  # 立即持久化

    def remove_by_a(self, a: str):
        """根据A删除记录"""
        if a not in self.a_to_bc:
            raise KeyError(f"A 值 '{a}' 不存在")

        b, c, _ = self.a_to_bc.pop(a)
        del self.bc_to_a[(b, c)]
        self.c_counter[c] -= 1
        if self.c_counter[c] == 0:
            del self.c_counter[c]   # 清除计数为0的项，保持整洁
        self._save()

    def update(self, a: str, new_b: str = None, new_c: str = None, new_d: str = None):
        """
        更新已有记录的B和/或C和/或D值。
        - 若 new_b、new_c、new_d 均为 None，则不做任何改动。
        - 更新后必须保证 (new_b, new_c) 不与其它记录冲突（除非是自身原组合）。
        """
        if a not in self.a_to_bc:
            raise KeyError(f"A 值 '{a}' 不存在")

        old_b, old_c, old_d = self.a_to_bc[a]
        # 确定最终的新值
        final_b = new_b if new_b is not None else old_b
        final_c = new_c if new_c is not None else old_c
        final_d = new_d if new_d is not None else old_d

        # 无变化
        if final_b == old_b and final_c == old_c and final_d == old_d:
            return

        # 检查新(B, C)是否已被其他记录占用
        if (final_b, final_c) in self.bc_to_a and self.bc_to_a[(final_b, final_c)] != a:
            raise ValueError(f"(B, C) 组合 ('{final_b}', '{final_c}') 已被其他记录占用")

        # 更新索引
        self.a_to_bc[a] = (final_b, final_c, final_d)
        del self.bc_to_a[(old_b, old_c)]
        self.bc_to_a[(final_b, final_c)] = a

        # 更新C计数器
        self.c_counter[old_c] -= 1
        if self.c_counter[old_c] == 0:
            del self.c_counter[old_c]
        self.c_counter[final_c] += 1

        self._save()

    def update_d_by_a(self, a: str, new_d: str):
        """
        通过A更新对应的D值。
        """
        self.update(a, new_d=new_d)

    # -------------------- 扩展查询（可选，方便调试） --------------------
    def get_all_records(self):
        """返回所有记录列表，格式 [(A, B, C, D), ...] 按A排序"""
        return [(a, b, c, d) for a, (b, c, d) in sorted(self.a_to_bc.items())]

    def get_records_by_c(self, c: str):
        """
        根据C获取所有匹配的记录（便于查看详细信息，非核心需求）
        :return: [(A, B, C, D), ...] 列表
        """
        result = []
        for a, (b, cur_c, d) in self.a_to_bc.items():
            if cur_c == c:
                result.append((a, b, c, d))
        return result
