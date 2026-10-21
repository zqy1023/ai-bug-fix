# 转人工任务的逻辑与设计审查

范围：Run `e76afea77d9c4ae089517fd50100a0fb`，本次读取时已有 27 份 MANUAL 终态报告：16 份归因 prepare_evidence，11 份归因 locate_agent。读取步骤、原始 Agent 输出、工具事件、正式报告，并用当前代码做无外部调用的重放。本次未修改业务代码，未控制后台 Run，也未重跑或推送任务。

结构化重放证据：`runtime/reviews/2026-10-21/manual-report-audit.json`。

## 1. P1：共享源码的相对路径在工具层仍然被误拒绝

代码：`app/infrastructure/claude/session.py:167`、`:289`。

输出校验器已经支持从共享只读树解析文件，但工具层的 `_resolve_tool_path` 只把相对路径拼到任务工作区。任务区只复制目标 carrier，所以 `framework/...`、`common/...` 解析到不存在且未授权的任务区路径，不能落到共享树里实际存在、已经授权的文件。

用 DTDC 原有工作区及 allowed_read_roots 重放同一文件：

- Read(`framework/track_result/track_result_models.py`) -> deny。
- Read(共享树中该文件的绝对路径) -> allow。

DTDC、DuXiuExp 的审计工具事件已捕获实际 scope_denied；不是仅凭模型声称不可读。上次修复只覆盖最终候选/证据路径校验，没有修复调查时的工具解析。

建议：为 Agent 提供清晰的逻辑路径到真实只读目录映射。工具 hook 校验和 SDK 实际读取参数必须使用同一映射，不能仅将权限检查改为 allow 后仍读取不存在的任务区路径。写入仍限目标 carrier。

## 2. P1：只读工具拒绝信息要求 Locate 输出禁止的字段

代码：`app/infrastructure/claude/session.py:258`；对照 `app/prompts/templates/locate.md`、`app/agents/locate/models.py:62`。

通用拒绝文案明确要求输出 change_plan、scope=project 和完整文件内容；Locate 协议却禁止 change_plan。DuXiuExp 的 event_index=9 捕获了此拒绝文案，最终原始响应确实包含 change_plan，随后严格校验失败并转人工。

这不能简单归咎为模型不遵守协议：运行时工具返回也在诱导它违反角色契约。前次保留诊断字段的修复能避免根因被清空，但仍会终止自动修复，无法根除误转人工。

建议：拒绝信息按角色及操作区分。只读 Locate 只能收到可读位置、允许的证据字段和具体缺口，不能要求生成修复协议。

## 3. P1：候选调查文件被当成必须修改的文件，误升级修复范围

代码：`app/agents/locate/graph.py:345`、`:352`；`app/workflow/routes.py:155`。

Locate Prompt 定义 candidate_files 为“与问题直接相关的最小候选”，并没有声明其中每个文件都必须修改。但校验器只要看到一个允许读取的共享候选，就把 recommended_scope 强制改为 project。

DTDC 原始输出的 category=carrier、recommended_scope=carrier，根因明确描述 carrier 将空数组传给 postal_code。candidate_files 同时列出两个 carrier 文件和仅用于确认字段类型的 framework 模型。用当前代码重放后：LOCATED 保持不变，scope 从 carrier 改为 project，after_locate 返回 finalize_execution。也就是说，修复“文件不存在”后仍会转人工，只是理由变了。

Passport 同样把工具及格式化器列为候选，存在相同结构风险；是否已具备自动修复所需业务证据须另行判断，不能保证修复范围判断后一定可以自动修复。

建议：区分 investigation_files、source_evidence 和 proposed_edit_files。只按实际拟修改目标判写入范围；读过共享模型、旧实现或基类不能直接等同于要修改它们。

## 4. P2：继承调用链无法进入调查范围，框架和根因分类产生人为偏差

代码：`app/agents/policy.py:10`、`:34`；源码 `gathers/Envialia/main.py:2`、`gathers/Maritime/main.py:5`。

Envialia 直接继承 OnTime，Maritime 直接继承 MO。共享只读目录仅列 framework/gathers_base/gather_providers/common，不包含其他 carrier 的实现文件。两个子类都缺少真实请求/解析实现，模型因此看不到决定性代码，只能给出 shared、INSUFFICIENT_EVIDENCE 或 MANUAL。

现有限制下转人工符合权限策略，但这是调查能力设计不足：只读依赖和跨 carrier 写入被混为同一范围问题。报告里的 shared 可能只是父类恰好放在另一个 carrier 目录，并不能证明修复必须改共享框架。

建议：预检解析明确的 import/继承依赖，按有界深度加入必要的只读文件，并记录依赖链。仍不能自动扩大写入范围。依赖外部包 yq_common 的情况需要单独定义可审计的依赖源码证据入口，不能把未知实现写成确定事实。

## 5. P2：正式人工报告不能独立交接，丢掉调查证据和决策事项

代码：`app/workflow/steps/finalize_execution/node.py:42`、`:143`。

检查到的 27 份 MANUAL final.json 都没有 source_evidence 和 evidence_gaps 字段；同时没有完整保留 facts、定位状态、责任边界、候选、人工方案及具体待决策问题。内部 steps.json 虽然保留这些数据，推到 Git 的独立报告却只带根因字符串、摘要及通用 next_action。

AITWorldwide 是具体例子：定位输出指出 N/A 事件到底应保留无时间、跳过还是记无数据，需要产品决策；同时记录了未读取实现及缺少响应。最终 terminal_reason 优先变成“旧框架，转人工”，这些决定接手人如何处理的信息没有进入 final.json。

建议：将 manual_reason_code、诊断结论、策略阻断原因、证据与缺口、待决策事项、下一步验证要求分别持久化。旧框架规则可以作为策略原因，但不能代替业务根因和交接内容。不得把推断直接升级为“已确认根因”。

## 6. P2：修复阶段明确转人工后，不一定进入报告交付

代码：`app/workflow/wiring.py:772`；`app/workflow/routes.py:241`、`:284`。

此前新增的 manual 修复方案停止重试分支是必要的，但 `_analysis_only_task` 只检查 Locate、旧框架和 scope_escalation_required；没有检查 Repair 的 manual 方案。

重放：Locate=LOCATED/carrier、Repair.change_plan.scope=manual、终态 MANUAL、已有工作区，after_finalize 返回 __end__，不进入 prepare_delivery。结果是在本地生成了 final.json，但不会像其他转人工任务一样提交报告分支。此项为当前代码的确定性路由复现，未声称已有真实任务完成了该分支。

建议：统一生成结构化 disposition 及报告交付意图，供所有路由共用，避免每个路由从不同字段重新猜测。

## 7. P2：人工任务缺少补证后的恢复闭环

代码：`frontend/src/lib/lifecycle.ts:27`；`workbench/api.py:2121`、`:2128`。

前端重试只支持失败任务，后端也只接受 FAILED/FAIL；已发生 prepare_delivery 的任务进一步被禁止整任务重试。27 份报告中的已交付 MANUAL 任务，即使补齐 HTML、修正共享读取或修复了契约，也不能通过当前 Task retry 链路重新进入定位。

防止重复交付的保护应保留，但不应导致“转人工”成为无法继续的死胡同。单步执行并不等同于父图继续，也不能自动完成剩余验证链。

建议：在保留原报告和交付回执的前提下，支持有独立 attempt/evidence revision 的“补充证据后重新定位”；区分报告交付与源码交付，避免因为已交过报告而永久禁止调查恢复。

## 合理转人工与历史缺陷的界限

- 16 个 legacy_only 任务在当前策略下只分析不修复，这是明确规则；若要支持旧框架，应显式扩展框架能力，不应只删除路由条件。
- Australia 的代理 CONNECT 502、FastExpress 的 503、StarTrack 的代理/TLS 故障缺少决定性运行证据。不能凭 HTTP 错误就认定必须修改 carrier 源码。
- DuXiuExp 历史报告的根因被清空、共享候选被误报不存在，在前次已有部分修复；历史任务未重跑，不会自动更新旧报告。本次列出的工具路径、角色契约冲突、范围推导等问题仍存在于当前代码。
- JSE 535462 字符超过 Locate 50000 字符预算直接转人工，仍是已知的大证据处理缺口；本次未通过提高阈值掩盖问题。

建议先处理第 1～3 项，再完善第 5～7 项；第 4 项需要明确依赖只读策略。避免通过统一放开目录或无条件降低 LOCATED 标准提高表面成功率。
