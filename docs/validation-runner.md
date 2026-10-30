# 采集复现与回归环境

平台服务和模型 SDK 使用项目 `.venv`。采集代码可通过
`AI_REPAIR_RUNTIME_PYTHON` 指定独立解释器绝对路径；留空才沿用平台解释器。
不要把源项目固定的旧 FastAPI/AnyIO 依赖安装到模型服务环境。

验证进程按源服务入口顺序初始化 LogUtils 和进程内指标，不启动 HTTP 或订阅。
依赖日志写入受控 stderr 或临时项目日志，外部 SSLKEYLOGFILE 不传入验证进程。
临时项目与字节码缓存建立在 runtime/execution-temp 下，确保采集子进程有写入权限；
不再依赖本机用户 Temp 目录的跨进程权限。
每份执行 JSON 回执记录实际解释器、Python 及关键依赖版本；版本不匹配必须作为限制报告。

运行资产来自固定源码提交，不生成空映射、假规则包或替身模块。
共享快照保留 py_rules_new.zip、local_provider_mapping.csv 等框架必需资产，
认证 CSV 仍被排除。过滤规则升级只用于新快照，不修改已执行批次的基线。

当前 Windows 复验解释器为 `.runtime-venv/Scripts/python.exe`，通过
`python -m venv --system-site-packages .runtime-venv` 建立，因此复用了本机已安装采集依赖；
仅在该环境覆盖安装源项目 requirements_prod.txt 声明的 yq_common 0.0.90。
这不是独立生产镜像；完整包清单保存在 runtime/audits/runtime-requirements.lock.txt，
跨机器重建须安装该清单并核对执行回执，不能只复制解释器路径。

内置验证分别执行原响应基线回放、补丁回放及原请求。
进程退出成功不代表业务正确：回放必须证明原异常，修复必须有对应业务断言。
现有默认断言只批准返回有效轨迹；正确业务错误或 NotFound 需具体业务断言和独立审查，
不得因上游恢复或报错消失声称验证通过。固定自定义命令由可信运维配置
AI_REPAIR_VALIDATION_COMMANDS 指定，必须接收 `{input_path}`，不允许模型选择命令。

无法复现的前置检查直接归为已跳过；模型调用失败归为失败。两者保留本地审计，
不发布空报告分支。有实质诊断结论但不修改源码时才进入无改动交付。
