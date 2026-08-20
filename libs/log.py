"""统一日志配置。

模块加载时即给 ``MJ`` 命名 logger 挂好 handler, 无需调用方在 __main__ 里初始化 ——
无论 main.py 是直接运行还是被导入 (如压测脚本 import main), INFO 日志都能正常输出。

级别分流:
- INFO      → stdout (启动、审计、生命周期)
- WARNING+  → stderr (可降级运营事件、自身故障)

设 propagate=False, 不冒泡到根 logger, 避免波及 httpx 等第三方库的日志输出。

保留原有方括号前缀 (如 ``[JOIN]``, ``[DENY]``) 写入消息体, 兼容现有按前缀 grep 的
运维习惯, 同时获得级别过滤、时间戳与分流能力。logging 模块内部持锁, 保证单条记录
原子输出, 适合多线程服务 (ThreadingHTTPServer + checkpoint 后台线程 + fan-out 池)。
"""
import logging
import sys

LOGGER_NAME = "MJ"


class _BelowWarning(logging.Filter):
    """只放行 WARNING 以下的记录, 用于让 stdout handler 不重复输出高级别日志。"""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < logging.WARNING


def _build_logger() -> logging.Logger:
    log = logging.getLogger(LOGGER_NAME)
    if log.handlers:  # 已配置 (如被重复 import), 不重复挂 handler
        return log
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.INFO)
    stdout_handler.addFilter(_BelowWarning())
    stdout_handler.setFormatter(formatter)
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(formatter)
    log.addHandler(stdout_handler)
    log.addHandler(stderr_handler)
    log.setLevel(logging.INFO)
    log.propagate = False
    return log


logger = _build_logger()


def get_logger() -> logging.Logger:
    """返回项目统一 logger。模块加载时已配置完毕, 任意时刻调用均可用。"""
    return logger
