# 最近一轮执行诊断

快照时间：2026-10-21 13:03:12（Asia/Shanghai）。
Run：`e76afea77d9c4ae089517fd50100a0fb`。
本次仅排查并保存证据，没有修改业务代码、停止 Run、重新执行 Agent 或推送分支。

## 当前进度

- Run 正在运行，心跳正常，尚无 Run 终态报告。
- 识别任务 65 个，派发 45 个：22 个 MANUAL、18 个 FAILED、4 个 SKIPPED、1 个 running；剩余 20 个尚未派发。
- 当前任务：`Ideexp__item__723b1b30eb2c`，处于 locate_agent。
- 已记录 22 次分析报告分支推送，未见源码修复交付成功。
- 日志阶段已于 11:38:23 完成，耗时 846.33 秒（约 14 分 6 秒）：6,738 hits，5,312 条可解析，1,426 条无法解析，去重后 284 个问题。
- LangGraph 在 13:02:38 的父检查点 task_cursor=44，task_results=44，与 45 个已派发/1 个运行中一致；不是整个 Run 卡死。

结构化快照：`runtime/reviews/2026-10-21/latest-run-audit.json`。

## 已确认的缺陷

### 1. P1：生成左右对照产物时字符串被当成 Path，批量阻断源码修复

位置：`app/workflow/steps/check_changes/node.py:185-206`、`node.py:167-169`。

`WorkspaceManager.locate_carrier_roots()` 返回相对路径 `list[str]`，`_render_file_contents()` 却直接将其中的字符串传给 `_under()`。后者调用 `root.resolve()`，抛出 `AttributeError: 'str' object has no attribute 'resolve'`。

截至快照，有 12 个任务在该节点失败：Brazil、Greenland、LHJ、ShreeMaruti、UPS、CCTCanada、CMACGM、CoscoShipping、Deppon、GLS、Geodis、HRXPL。其源头是流水线的展示产物生成错误，不能据此认定 Agent 修复代码无效。

已在独立临时目录直接调用当前 `_render_file_contents()` 复现；堆栈为 `_render_file_contents:206` → `_under:169`。

此前测试覆盖差异文本与门禁判定，没有覆盖真实节点生成左右对照产物的路径，因而漏检。

建议：统一转换成工作区内的绝对 Path 后进行范围校验；增加真实 check_changes 节点产物生成回归测试。优先级最高。

### 2. P1：共享候选文件的查找范围与只读授权范围不一致

位置：`app/agents/locate/graph.py:317`。

候选文件先调用 `_relative_file(root, value)`，只检查 Task 工作区中是否存在，尚未查找共享只读树便会抛异常。源码证据的 `_evidence_target(..., shared_root)` 却支持共享树回退，形成两套不一致的路径语义。

实证：Australia、DTDC、Envialia、Turkey、DHLGroup 的定位输出出现候选路径缺失错误。临时目录中创建共享 `common/helper.py` 后，`_evidence_target()` 能找到文件，`_validate_output_paths()` 仍报告不存在。

Australia 等任务可能本来也因下游故障需要转人工；这里确认的是路径校验附加了错误原因，并非五个任务都应自动修复。

建议：候选与源码证据使用统一的路径解析/存在性/只读授权校验，保持写入仍限于目标 carrier。

### 3. P2：一个额外输出字段导致合法诊断被全部清空

位置：`app/agents/locate/graph.py:225-249`。

DuXiuExp 的原始 Agent 产物包含 204 字符根因、2 条源码证据及额外 `change_plan`。严格模型拒绝额外字段后，fallback 仍保留同一额外字段，再次失败，最终创建空白 LocateOutput。持久化结果 root_cause 长度 0、source_evidence 数量 0。

DXDelivery 也有同类输出契约错误。保留严格契约是合理的，但安全降级不应丢失已经收到的诊断事实；原始产物应可定位，合法字段可单独校验后留存。

### 4. P2：不可通过同输入解决的问题被反复自动重试

位置：`app/workflow/routes.py:after_repair`、`app/workflow/runner.py:_save_fail`。

Evdemolojistik 的定位报告已明确缺少真实 HTML；Repair 连续 3 次表示相同证据不足、没有修改，最后被记为普通 FAILED。其执行时间约为 12:45:47—12:51:59。

同时，5 个缺少源码目录的任务（YSF、ANDEXP、EDASUN、HGD、HTCN）也各自重复准备阶段 3 次。缺失源码本身可能是日志/仓库版本不匹配，不能都算程序 bug；但重试分类确有缺口。

建议：区分瞬时错误、程序错误、缺少证据和缺少目标源码。证据不足进入人工补充状态；不能通过重跑解决的问题不要重复调用模型。

### 5. P2：Run 的“当前阶段”长期停留在已完成的识别节点

位置：`app/infrastructure/records/store.py:_update_lifecycle`。

Run lifecycle.current_step 仍为 identify_carrier，而已派发 45 个任务且 Task lifecycle 指向 Ideexp.locate_agent。Task 步骤更新只写 task lifecycle，Run 的当前任务/节点投影没有同步；心跳正常不代表 phase 正确。

建议：Run 展示层区分父图当前节点 run_task、当前 task_id 和当前任务步骤；不要把上一次 Run 级原子步骤当作实时阶段。

### 6. P2：日志解析覆盖率和大字段处理不完整

- 1,426/6,738（约 21.2%）日志被 parse_hit 返回 None 丢弃，只有总数，没有失败分类或样例证据。不能把这些记录算作已完成问题分析。
- JSE 的原始问题长 535,462 字符，超过定位上下文 50,000 字符，因此未调用模型便转人工。大字段需要保留在 artifact，同时给模型结构化异常与按需证据；增加模型输入上限不是完整解决方案。
- 284 个去重问题按 biztype+carrier 选择最早代表问题，只生成 65 个任务。该规则符合当前单任务策略，但其余 219 个问题没有显式排队/延期状态；若业务目标是分析全部问题，应展示这部分覆盖差距。

### 7. P2：跳过原因被通用终态文案覆盖

UNI、XFExpress、YLEXP、ZYQTFS 的实际 repair_branch_preflight 因已存在分支跳过，但 final.json 只写“任务以 SKIPPED 结论结束，步骤记录没有给出更具体原因”。跳过异常没有 error 字段，而 output 又缺少 reason，导致真实异常说明丢失。

建议：所有业务终态都保存结构化 reason；SKIPPED 也应有专属终态解释，而非通用兜底。

## 运行版本与范围说明

当前 Run 元数据仍是 `log_days=1`，discover_logs 输入仍是 `days=1`，而不是新的 `log_hours`。因此这轮不是修改后的小时级执行，不应将 6,738 hits 等同于“近 24 小时全部日志”。旧实现还包含服务名、业务类型及错误排除条件，其查询范围和外部日志平台展示口径可能不同。

这能证明该轮使用的查询输入是旧格式；没有以此推断所有磁盘最新修改都已经由运行中进程加载。

## 建议顺序

1. 先修左右对照产物的 Path 类型错误，解除源码修复的共同阻断。
2. 统一共享路径解析与授权校验，避免错误转人工。
3. 修契约降级时的诊断保留、不可恢复错误分类及终态原因。
4. 完善 Run/Task 当前阶段投影与全量日志覆盖统计。
5. 当前 Run 未结束，暂不覆盖其中的失败记录；后续恢复应保留原始证据和分支状态。
