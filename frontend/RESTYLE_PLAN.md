# 前端样式架构重做方案(RESTYLE_PLAN)

> 2026-10-25：用户已确认改用 Stitch 四页设计。当前实现与视觉规范见 [DESIGN.md](DESIGN.md)，以下保留为早期架构分析记录。

> 日期:2026-10-24。结论先行:**底子比预期好,不需要推倒重来;需要的是"架构升级 + 令牌收编",分四阶段渐进完成,全程视觉零回归。**

---

## 一、现状评估

### 已经成熟的部分(保留,不要动)
- **令牌单源**:`src/app/tokens.ts` 是唯一色板来源,同时驱动 CSS 变量(`--wb-*`)和 Ant Design `ThemeConfig`——这是很多项目都没有的纪律。
- **设计原则成文**:`DESIGN.md` 明确了密度、语义化状态(颜色+图标+文字三重编码)、无障碍(:focus-visible、aria-current、prefers-reduced-motion)、响应式断点(1440/768/390)。
- **组件数据层干净**:零 `any`、tsc 零错误、react-query + SSE 数据流规范。

### 不成熟的三层(本次要解决的)
| 层 | 问题 | 量化证据 |
|---|---|---|
| **作用域层** | 12 个全局 CSS 文件共 2453 行,扁平类名(`case-hero`、`filter-bar`)靠命名约定防冲突,无真正作用域;样式与组件分离,改一处要在两个文件间跳 | `import './styles.css'` 全局一次性引入 |
| **令牌层** | 设计值存在大量"逃逸":游离 hex、直写 rgba、`!important` 压制 antd | **312 处**裸 hex(#f8fafc×16、#fbfcfe×12、#15803d×11…)、**17 处** rgba、**23 处** `!important`;`base.css` 用 `.ant-*` 选择器做本该由 ThemeConfig 完成的定制 |
| **组件原语层** | 空态、筛选栏、页头计数、状态徽标等重复模式靠全局类复制,没有带 props 的 React 原语 | `EmptyState`/`StatusBadge` 已有组件化苗头,但 PageHeader/FilterBar/KeyValueList 等仍是类名拼接 |

直接后果:换肤/加暗色模式需要全文件排查硬编码色;新页面复制粘贴旧类名;antd 升级时 `.ant-*` 覆盖可能静默失效;类名冲突风险随代码量线性增长。

---

## 二、方案对比

### 方案 A:四阶段渐进重构(推荐)
保持 Ant Design + 现有视觉语言,架构升级为 **语义令牌 + CSS Modules + 组件原语层**。每阶段独立可验收、可随时停止,视觉零回归。

### 方案 B:迁移 Tailwind CSS v4(不推荐)
utility-first 迭代快,但与 antd 是两套范式并存;2453 行样式全部重写;团队心智切换成本高;对"工程工作台"这种重表格重表单的中后台,antd 原语 + 令牌仍是更短路径。

### 方案 C:换组件库全面重设计(仅当想换视觉身份)
成本/风险最高,只有当"成熟"指的是"完全不同的外观"时才考虑。本方案 A 的阶段 4 提供了不换库的视觉升级路径。

---

## 三、方案 A 详细设计

### 阶段 1:语义令牌 2.0(约 1 天)
**目标:312 处裸 hex 归零,antd 覆盖归位,暗色模式变成"只改一层"。**

令牌分三层:
```
tokens.ts
├─ primitive   原色板(现有 palette,保持)
├─ semantic    语义层:bg/surface/text/border/status × success|danger|warning|running
│              例:status.danger.ink / status.danger.soft / status.danger.line
│              现散落的 #b91c1c、#fdf3e3、#b45309 等状态色全部收编到这里
└─ elevation   阴影/浮层/焦点环(现 rgba 直写收编)
```
- CSS 变量按 `--wb-semantic-xxx` 命名输出;`[data-theme="dark"]` 只覆盖 semantic 层 → 暗色模式在此阶段"就绪"但不启用。
- `base.css` 里 `.ant-*` + `!important` 的 23 处覆盖,逐条迁入 `theme.ts` 的 `components` 配置(antd v5 token 化定制),迁不掉的收敛到一个带注释的 `antd-overrides.css`。
- 验收:全局 `grep -E "#[0-9a-f]{3,8}"` 仅剩 tokens.ts;tsc + build 通过;1440/768/390 三档截图与改造前对比一致。

### 阶段 2:CSS Modules 迁移(约 2-3 天)
**目标:样式与组件同址、真正作用域化。**
- 每个组件配同名 `Xxx.module.css`,从对应全局文件机械搬移 + 类名改 `styles.xxx`;
- 全局层只保留四件事:`base.css`(reset/排版/焦点环)、`tone.css`(状态变量注入,7 个 tone 类)、`antd-overrides.css`(遗留覆盖)、`variables` 注入;
- 迁移顺序按页面隔离:runs → run → task → approvals → agent/diff,每页迁完即验收该页;
- 加 **stylelint** 门禁:全局层禁止新增类(白名单制)、全库禁止 `!important` 新增、禁止裸 hex(必须 var())。
- 验收:build 产物无全局类名新增;每页三档截图对比。

### 阶段 3:组件原语层(约 2-3 天)
**目标:页面从"类名拼接"升级为"组件组合"。**
新建 `src/components/ui/`:
- `PageHeader`(标题/描述/计数/操作区)、`FilterBar`、`Panel`(卡片面板)、`KeyValueList`(本轮信息 dl 结构)、`SectionNav`(执行路径侧栏)、`CodeBlock`(等宽滚动块);
- 原语只吃语义 props(`tone`、`density`、`emphasis`),内部用 module css + 语义令牌;
- `StatusBadge`/`EmptyState` 收编进 ui/ 并统一变体 API;
- 现有页面逐个替换调用点,删掉对应全局类。
- 验收:各页面 DOM 结构等价(类名变,视觉不变);新页面接入成本 = 组合原语,不再写 CSS。

### 阶段 4:视觉打磨与主题扩展(约 1-2 天,可选)
**目标:在 DESIGN.md 约束内提升成熟观感,不改变"安静工程台"定位。**
- 字阶统一:目前 h1 用 clamp、h2 固定 20px、正文 14px、元数据 12/11/10.5px 混用 → 收敛为 5 档字阶令牌;
- 状态色和谐:7 个 tone 的 ink/soft 色对用统一明度公式生成(color-mix),消除手工配色漂移;
- 暗色模式启用(阶段 1 已就绪):`data-theme` 切换 + 跟随系统;
- 微动效规范:仅保留 status-spin,新增列表 fade-in 统一为 120ms,尊重 reduced-motion;
- 可选:Storybook 只为 ui/ 原语建站,作为组件字典与视觉回归基线。

---

## 四、风险与对策

| 风险 | 对策 |
|---|---|
| 类名迁移遗漏导致样式丢失 | 每页迁移后立即截图对比(1440/768/390);全局类白名单由 stylelint 强制 |
| antd 覆盖迁移后视觉差异 | 逐条迁移逐条核对;ThemeConfig 不支持的项留在 antd-overrides.css 并注释原因 |
| 迁移期间功能开发并行冲突 | 阶段 2 按页面隔离,与功能改动不同文件;每个阶段一个独立提交 |
| 原语抽象过度 | 原语只在 ≥2 处真实复用时提取;单处使用的样式留在组件 module 里 |

## 五、不做的事

- 不换组件库、不引入 Tailwind、不引入 CSS-in-JS 运行时(antd + Modules 是当前团队规模下维护成本最低的组合);
- 不改数据层(queryKeys/workbenchQueries/workflow.generated.ts 契约链);
- 不做装饰性改版(DESIGN.md 明确反对宣传式视觉)。
