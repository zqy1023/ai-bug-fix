"""运行时产物与源码改动的区分口径（全系统唯一实现）。

采集框架运行时会在 ``logs/`` 下按服务名创建日志文件。这些文件既不是模型写的，
也不是本次修复的产物，任何"源码是否被改动"的判定都必须把它们排除：

- ``source_manifest`` / ``source_changes``：决定 ``changed_files``，进而决定
  ``unauthorized``（越权写入）与交付清单。
- ``validation._validation_changes``：决定验证命令是否污染了被验证源码。

此前两处各写一套：``diff.source_manifest`` 完全不排除 ``logs/``，而
``validation`` 硬编码了一条 ``logs/gather\\.py\\.track_*.log`` 正则。结果同一个
运行日志在一个地方被当成越权源码改动，在另一个地方被正确放行。这里统一。

判定刻意保守，只认**执行后新建**的、``logs/`` 根目录下按当前服务名派生的日志：

- 新建的匹配日志 → 运行时产物，不算源码改动；
- **已存在的日志被修改或删除 → 仍算源码改动**（有人动了文件，必须核验）；
- ``logs/xxx.py``、其他目录、其他文件名 → 仍算源码改动。
"""

from __future__ import annotations

import re
from functools import lru_cache

from config.settings import settings

# 框架日志器写入的目录（工作区相对路径，POSIX 分隔符）。
LOG_DIRECTORY = "logs"


@lru_cache(maxsize=8)
def _log_pattern(service_name: str) -> re.Pattern[str]:
    """按服务名派生日志文件名匹配式。

    ``gather.py.track`` → ``logs/gather\\.py\\.track-2026-10-30_12-00-00_123.log``
    也接受 ``_`` 作为服务名与时间戳之间的分隔符，因为框架不同版本用过两种写法。
    """
    name = (service_name or "").strip()
    if not name:
        return re.compile(r"(?!)")  # 永不匹配：没有服务名就不放行任何日志
    return re.compile(
        r"^"
        + re.escape(LOG_DIRECTORY)
        + r"/"
        + re.escape(name)
        + r"[-_]\d{4}-\d{2}-\d{2}[_-]\d{2}-\d{2}-\d{2}_\d+\.log(?:\.\d+)?$"
    )


def runtime_log_service_name() -> str:
    """当前运行配置里的日志服务名；读取失败时退回项目默认值。

    只在真正需要判定时调用，避免把 ``load_config`` 的副作用带进模块导入。
    """
    try:
        from infrastructure.llm.config import load_config

        return str(load_config().log_service_name or "").strip()
    except Exception:
        return str(getattr(settings, "default_service_name", "") or "").strip()


def is_runtime_log_path(name: str) -> bool:
    """按路径/文件名判断是否为框架运行时日志，不看新旧。

    用于"源码内容身份"计算：日志文件不该参与 ``patch_id`` 指纹，否则框架在
    审查与交付之间新写一条日志，就会把补丁判成"已变化、证据失效"。
    """
    key = str(name or "").replace("\\", "/").lstrip("/")
    if not key:
        return False
    return bool(_log_pattern(runtime_log_service_name()).match(key))


def is_new_runtime_log(name: str, before: dict[str, str], after: dict[str, str]) -> bool:
    """判断某个路径是否为本次执行新产生的框架运行时日志。

    只有执行前不存在、执行后出现、且文件名匹配当前服务名时成立。
    已存在的日志被改动不属于运行时产物，仍要当作源码改动核验。
    """
    key = str(name or "").replace("\\", "/").lstrip("/")
    if not key or key in before or key not in after:
        return False
    return bool(_log_pattern(runtime_log_service_name()).match(key))


def classify_changes(
    before: dict[str, str], after: dict[str, str]
) -> tuple[list[str], list[str]]:
    """把两份 manifest 的差异拆成「源码改动」与「新建运行时日志」。

    供 ``diff.source_changes`` 与 ``validation._validation_changes`` 共用，
    保证越权判定、交付清单与验证完整性检查使用同一套口径。
    """
    changed: list[str] = []
    runtime_files: list[str] = []
    for name in sorted(before.keys() | after.keys()):
        if before.get(name) == after.get(name):
            continue
        if is_new_runtime_log(name, before, after):
            runtime_files.append(name)
        else:
            changed.append(name)
    return changed, runtime_files


__all__ = [
    "LOG_DIRECTORY",
    "classify_changes",
    "is_new_runtime_log",
    "is_runtime_log_path",
    "runtime_log_service_name",
]
