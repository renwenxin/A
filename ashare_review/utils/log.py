"""统一日志配置模块

用法:
    from ashare_review.utils.log import get_logger
    logger = get_logger(__name__)
    logger.info("message")
    logger.warning("something wrong")
    logger.error("error detail", exc_info=True)

配置优先级:
    1. 环境变量 LOG_LEVEL (DEBUG/INFO/WARNING/ERROR)
    2. 默认 INFO
"""

import logging
import os
import sys

_logging_initialized = False


def _init_logging():
    """初始化全局日志配置（幂等）"""
    global _logging_initialized
    if _logging_initialized:
        return

    level_name = os.environ.get('LOG_LEVEL', 'INFO').upper()
    level = getattr(logging, level_name, logging.INFO)

    # 简洁格式：时间 级别 模块: 消息
    fmt = '%(asctime)s %(levelname)-7s %(name)s: %(message)s'
    datefmt = '%H:%M:%S'

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))

    root = logging.getLogger()
    root.setLevel(level)
    # 避免重复添加 handler
    if not root.handlers:
        root.addHandler(handler)

    # 降低第三方库日志噪音
    for lib in ('urllib3', 'httpx', 'httpcore', 'asyncio', 'matplotlib'):
        logging.getLogger(lib).setLevel(logging.WARNING)

    _logging_initialized = True


def get_logger(name: str = None) -> logging.Logger:
    """获取配置好的 logger 实例"""
    _init_logging()
    return logging.getLogger(name)


# 模块加载时自动初始化
_init_logging()
