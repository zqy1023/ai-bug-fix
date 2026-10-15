"""两个独立 agent 子图。

- ``app/agents/repair`` 修复：analyze → execute
- ``app/agents/review`` 审查：review

两边不共享状态、配置和 provider。主图只接收各自 ``graph.build`` 编出来的图。
"""
