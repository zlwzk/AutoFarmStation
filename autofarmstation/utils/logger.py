"""日志模块.

- 主日志: %APPDATA%\\AutoFarmStation\\logs\\autofarmstation.log
- 自动轮转(单文件 2MB,保留 5 个)
- 所有写入自动过 Sanitize 脱敏
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from .paths import user_log_dir
from .sanitize import sanitize

_LOGGER_NAME = "autofarmstation"
_LOG_FILE = "autofarmstation.log"


class _SanitizeFilter(logging.Filter):
    """日志过滤器:对所有 record 的 getMessage() 结果做脱敏."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            sanitized = sanitize(msg)
            # 直接覆写 formatted message
            record.msg = sanitized
            record.args = ()
        except Exception:
            pass
        return True


_INITIALIZED = False


def setup(level: int = logging.INFO) -> logging.Logger:
    """初始化日志(幂等,只生效一次)."""
    global _INITIALIZED
    logger = logging.getLogger(_LOGGER_NAME)
    if _INITIALIZED:
        return logger
    _INITIALIZED = True

    logger.setLevel(level)
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    sf = _SanitizeFilter()

    # 控制台
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(level)
    sh.setFormatter(fmt)
    sh.addFilter(sf)
    logger.addHandler(sh)

    # 文件(自动轮转)
    log_path: Path = user_log_dir() / _LOG_FILE
    try:
        fh = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=2 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        fh.setLevel(level)
        fh.setFormatter(fmt)
        fh.addFilter(sf)
        logger.addHandler(fh)
    except Exception:
        # 文件日志失败不影响控制台
        pass

    return logger


def get() -> logging.Logger:
    """获取全局 logger(自动 setup)."""
    if not _INITIALIZED:
        setup()
    return logging.getLogger(_LOGGER_NAME)