"""一次性运行器：用 Kibana Console Proxy 顶替直连 ES，跑完整流程。

**这不是项目代码**：仅用于腾讯云 ES 暂时不可用时验证整条链路。腾讯云恢复后
直接 ``python server.py`` 即可，不需要本文件。

它做的事只有一件：在进程内替换 ``create_es_client`` 的返回值，让
``ESLogAdapter`` 拿到走 Console Proxy 的客户端。其余流程、模型调用、审查、
交付全部按项目原样执行。

用法::

    AI_REPAIR_KIBANA_PASSWORD=<密码> python docs/run_with_kibana_es.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _patch_es_client() -> None:
    """把 ES 客户端工厂换成 Console Proxy 版本（仅本进程内生效）。

    ``workflow.engine.graph`` 在函数体内 ``from infrastructure.llm.config import
    create_es_client``，因此只要替换配置模块上的属性，运行时取到的就是临时客户端。
    """
    from docs.kibana_es_client import client_from_env

    import infrastructure.llm.config as config_module

    proxy = client_from_env(timeout=int(os.getenv("AI_REPAIR_ES_TIMEOUT", "60")))

    def create(config):  # noqa: ANN001 - 与被替换的工厂保持同形
        return proxy

    config_module.create_es_client = create


def main() -> int:
    password = os.getenv("AI_REPAIR_KIBANA_PASSWORD", "")
    if not password:
        print("缺少 AI_REPAIR_KIBANA_PASSWORD 环境变量", flush=True)
        return 2
    _patch_es_client()
    print("已切换到 Kibana Console Proxy 临时 ES 客户端", flush=True)
    # 项目里同时存在 ``server`` 包和 ``server.py`` 入口文件，直接 ``import server``
    # 拿到的是包。按路径加载入口文件，才是项目自己的启动逻辑。
    import importlib.util

    spec = importlib.util.spec_from_file_location("ai_bug_fixer_entry", ROOT / "server.py")
    if spec is None or spec.loader is None:
        print("无法加载 server.py 入口", flush=True)
        return 2
    entry = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = entry
    spec.loader.exec_module(entry)
    return entry.main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
