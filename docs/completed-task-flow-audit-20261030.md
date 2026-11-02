# 已完成任务流程审计（2026-10-30）

批次：`bab1219038e84da68be17cfaa778d2b6`。本次固定审计已取得终态报告的 6 个任务；批次仍在继续。

## 核对方法

读取当前 API 批次摘要、每个任务的 `workflow/outputs/*.json`、`reports/final.json`、`audit/prompts/*.json`、有效策略及源码快照。使用 TaskSnapshot 还原当前工作区，并用 source_changes 对比基线；核对已提交 commit 的文件清单。未重新调用模型或承运商接口。

所有 6 个任务的实际源码差异均为空。所有审查记录的 replay/request 均为 unverified。

| 任务 | 实际根因及权限 | 最终结果 | 发布内容 | 分析/审查耗时（秒） |
| --- | --- | --- | --- | --- |
| ACSWorldwideExpress | 验证码请求连接失败，无响应；gathers 可写，但没有必须修改的本地缺陷证据 | SKIPPED | 未发布 | 76.9 / 46.0 |
| CuPostKr | 空正文进入 HTML 解析后失败；spiders 只读 | SKIPPED | 未发布 | 85.9 / 68.3 |
| Intras | 正文仅有 DOCTYPE，不能形成轨迹页；spiders 只读 | SKIPPED | 未发布 | 44.1 / 107.3 |
| EmuExpress | Storetake 状态缺少映射导致 KeyError；spiders 只读，映射语义待确认 | UNCHANGED_DELIVERED | 仅 task_summary.md | 29.0 / 76.5 |
| SmithLogistics | 明确的 Shipment not found 对象被按数组取下标；spiders 只读 | UNCHANGED_DELIVERED | 仅 task_summary.md | 31.5 / 61.4 |
| TWSExpress | 过滤空轨迹后对空列表取下标；spiders 只读，空轨迹结果语义待确认 | UNCHANGED_DELIVERED | 仅 task_summary.md | 44.0 / 93.1 |

外部触发归因与只读处置总体有依据。CuPostKr/Intras 的 HTTP 状态和具体异常栈是源码条件推导，不能表述为已取得运行证据；它们未修改代码，不构成修复成功。TWSExpress 没有凭空将空事件映射成 NotFound，这一边界处理正确。

已核对的分析报告提交：

- EmuExpress：`11fa0e3f26aaaff90f241ce85bd94f3f3a9520f7`，仅 `Src/gather_spider_track/spiders/EmuExpress/task_summary.md`。
- SmithLogistics：`6c351556796ccd7cbc6e7f49d3a546ad82ac61bb`，仅相应 carrier 的 `task_summary.md`。
- TWSExpress：`f17968ab5e923f12d1413b9215e5c217da2ea860`，仅相应 carrier 的 `task_summary.md`。

## 审查跳过的必要性

模型提出的是跳过建议，独立审查核对是否误把本地缺陷归给上游；批准后才应形成最终跳过。已有重复分支、明确关闭分析策略等平台确定性跳过可以直接收尾。二者不宜混成同一种代码审查。

实际 review_mode 为 analysis_disposition，预算为 12 轮、16 工具、300 秒。六次审查总耗时约 452.6 秒（7.5 分钟），分析阶段约 311.4 秒（5.2 分钟）。审查确实读取了源码与相关分支，但只读任务仍重复查找共享目录和包装入口，成本偏高；应直接提供已确认的源码归属与证据引用，审查自主核验关键条件。

## 确认的问题

1. **跳过理由未完整交接。** `core/contracts.py:212` 的 RepairHandoff 未声明 skip_reason，extra=ignore 将其丢弃；六份真实 review prompt 的 evidence.repair 均无此字段，三份跳过任务 task_prompt 也未携带这个字段。审查仍可从 summary/root_cause 推断意图，但无法逐项审查完整的跳过声明。
2. **审查对象和修复结果混用。** 无改动处置与补丁共用 code_verdict、business、semantic_verified；review graph 将“不修改的处置有依据”折算为 semantic_verified=true，前端显示“代码判断通过/业务语义已确认/业务验证通过”。TWSExpress 和 EmuExpress 的报告同时明确修复所需业务语义尚未知，说明标签与实际审查对象不一致。批准分析报告不等于已修复，也不等于业务运行验证通过。
3. **终态可以绕过审查失败。** `core/task_outcome.py` 在检查 review 前直接返回 SKIPPED/CANCELLED。最小重现：传入相同的 skip_reason 和 FAIL 审查，status=SUCCESS 得 FAILED，status=SKIPPED 却得 SKIPPED。当前六项审查全部 PASS，未在这六项中触发此错误；但投影重算与恢复路径存在错误风险。
4. **未修复缺陷与 TODO 混用。** TWSExpress 等报告把已确认、因只读权限未修复的缺陷及必要语义决策放进 todo，而 findings 为空。其不阻断“分析报告交付”可以成立，但前端因此显示没有发现代码缺陷，容易遗漏后续处理。需要独立保留已确认的未修复问题，TODO 仅承载可选回归建议。
5. **终态含义缺少醒目说明。** 当前 SKIPPED 的实际含义是“已分析/审查，决定不进入源码修复及分支发布”，并非没执行任务；UNCHANGED_DELIVERED 则是实际发布了分析报告。四种终态可以保留，但要展示跳过原因、交付物类型、缺陷是否解决与真实验证状态，不能仅靠一个状态徽章解释全部事实。

## 应采用的规则

- 模型跳过建议 → 处置审查 → 批准才 SKIPPED；否决返回分析修复，无法判定或执行失败最终 FAILED。
- 平台确定性排除 → 记录具体原因 → SKIPPED，不调用无必要的模型审查。
- 有补丁 → 补丁审查 → 实际交付成功才 CHANGED_DELIVERED。
- 无补丁但有有效分析报告 → 分析审查 → 实际交付成功才 UNCHANGED_DELIVERED，并明确“分析已交付、缺陷未修复”。
- 审查类型、审查结论、缺陷解决状态、运行验证状态分别记录和显示；业务运行未执行不能显示已验证。

## 修复与验证记录（2026-10-30）

上述交接、处置审查、终态与展示问题已完成平台修改：RepairHandoff 保留 skip_reason；审查按实际源码差异确定 patch/analysis_disposition；处置通过使用 disposition_verified，不再声称源码修复或运行验证通过；unresolved_issues 独立于可选回归 TODO。模型跳过未获有效独立批准时不得成为 SKIPPED，失败审查也不能被已有终态覆盖。

无响应不再构成禁止修复的条件。已有请求、日志、配置和源码证明本地缺陷时可以直接修复；只有定向核对后仍无法定位才补充复现。明确服务错误回执进入正常解析导致额外异常时，应局部按现有 ServiceError 协议处理；已正确处理的纯外部故障才跳过。不能把所有 HTML、解码失败或无响应一律转 ServiceError。

额外核对 Abcourier 的真实归档：分析 Agent 曾明确引用“上游错误包装成解析错误不足以修改”的旧规则，将 HTML 503 进入 json.loads 判为纯上游跳过。该规则表述过宽，已在公共诊断和独立审查提示词中收窄；原任务的审查调用失败，最终为 FAILED，未交付承运商补丁。本次修改平台规则和流程，不将其记为 Abcourier 已完成源码修复或发布。

验证结果：

- 平台全量测试此前为 269 passed；最后两处审查约束及提示词澄清后，针对性交接、审查、分流与终态回归为 56 passed。
- 新增编译图回归覆盖：误判上游并建议跳过 → 审查否决并传回缺失的 503 处理依据 → 回修清理旧结论 → 新补丁审查通过 → 才能提交；该用例验证编排，不冒充真实模型效果。
- 前端构建通过；Playwright 核对六个真实任务页面、新字段、未解决缺陷、移动端布局及浏览器异常，均通过。回执：`runtime/ui-check/disposition-review/receipt.json`。
- 运行状态快照：`runtime/audits/disposition-fix-20261030/live-before.json` 与 `live-after.json`。
- 新批次真实 Intras 调用已确认加载诊断规则 1.8.1 与审查提示词 10.0.1；审查产物为 review_kind=analysis_disposition、disposition_verified=true、semantic_verified=false，replay/request 均为 unverified。原始取样保存在 `runtime/audits/disposition-fix-20261030/new-run-review-sample.json`；这证明新交接与展示语义已在真实调用生效，不代表 Abcourier 已修复。

运行期间观察到服务已于 18:24 重新启动，命令为项目 `.venv` 解释器启动 `server.py --frontend`，早于本次收尾检查；此启动方式同时创建新批次 `2e3b6ffbb0bc4a6d96f73e0349854b14`。新进程启动晚于后端修复文件更新时间；提示词在每次调用时重新从磁盘读取。旧批次 `bab1219038e84da68be17cfaa778d2b6` 已因 WORKER_HEARTBEAT_TIMEOUT 结束，新批次正在执行，因此本次收尾未再重启服务、未重复创建批次，也未并行恢复旧批次。后续仅重载服务应使用 `server.py --frontend --serve-only`。

已归档的六个任务保留原执行事实；前端对审查含义的纠正不代表这些任务重新经过模型审查。新批次仍在执行，不能据测试通过声称所有任务已在新规则下正确修复。
