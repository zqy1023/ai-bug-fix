"""隔离 Git 回归环境的本地代理配置。"""
import os
import re
from pathlib import Path

from dotenv import dotenv_values

from core.errors import StepFailed

def prepare_regression_environment(project: Path) -> dict[str, str]:
    """只在调用方创建的回归克隆中更新本地 env，保留其它配置。

    ``PROXY_MANAGER_ADDRESS`` 未配置时直接失败：真实请求回归没有代理地址
    只会得到"环境看似就绪、请求全部失败"的假结论，所以这里不再写 .env。
    """
    from config.nacos import nacos_get
    from config.settings import settings
    address = nacos_get("PROXY_MANAGER_ADDRESS", "").strip()
    if not address:
        raise StepFailed(
            "PROXY_MANAGER_ADDRESS 未配置，无法建立真实请求回归环境",
            result="manual",
            code="PROXY_MANAGER_ADDRESS_MISSING",
        )
    target = project / ".env"
    if target.is_symlink():
        raise ValueError("回归 .env 不允许是符号链接")
    lines = target.read_text(encoding="utf-8-sig").splitlines() if target.exists() else []
    # dotenv 支持 export、空白和带引号的值；删除同键旧声明，避免重复值歧义。
    lines = [line for line in lines if not re.match(r"^\s*(?:export\s+)?proxy_manager_address\s*=", line)]
    lines.append(f'proxy_manager_address="{address}"')
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if dotenv_values(target).get("proxy_manager_address") != address:
        raise ValueError("回归代理 env 写入校验失败")
    return {"source": ".env", "proxy_manager_address": address}
