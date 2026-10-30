"""权威执行阶段包。

物理目录与职责（阶段 3/4 由 ``agents/`` 下的 Agent 子图实现，不再保留空适配目录）：

- ``log_pipeline``      批次触发：日志发现、规范化与去重（discover/normalize/deduplicate/identify）
- ``prepare_context``   阶段1：源码快照、预检证据、隔离工作区
- ``claim_slot``        阶段2：修复资格领取（分支预检 + 互斥锁）
- ``generate_report``   阶段5：汇总生成报告与交付产物准备
- ``submit_changes``    阶段6：提交候选分支至远程 Git 仓库
- ``finalize``          收尾与异常转人工处理

各目录均为合法 Python 包名，直接以 ``workflow.stages.<name>`` 导入，不再依赖
``sys.modules`` 别名注入。
"""
