"""隔离 Git 回归环境的本地代理配置。"""
import os
from pathlib import Path

from dotenv import dotenv_values

PROXY_MANAGER_ADDRESS = os.getenv('PROXY_MANAGER_ADDRESS')


def prepare_regression_environment(project: Path) -> dict[str, str]:
    """只在调用方创建的回归克隆中更新本地 env，保留其它配置。"""
    target = project / ".env"
    if target.is_symlink():
        raise ValueError("回归 .env 不允许是符号链接")
    lines = target.read_text(encoding="utf-8-sig").splitlines() if target.exists() else []
    # dotenv 支持 export、空白和带引号的值；删除同键旧声明，避免重复值歧义。
    import re

    lines = [line for line in lines if not re.match(r"^\s*(?:export\s+)?proxy_manager_address\s*=", line)]
    lines.append(f'proxy_manager_address="{PROXY_MANAGER_ADDRESS}"')
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if dotenv_values(target).get("proxy_manager_address") != PROXY_MANAGER_ADDRESS:
        raise ValueError("回归代理 env 写入校验失败")
    return {"source": ".env", "proxy_manager_address": PROXY_MANAGER_ADDRESS}
