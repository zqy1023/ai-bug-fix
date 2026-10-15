import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  Activity, AlertTriangle, Archive, ArrowDownToLine, ArrowUpRight, Check,
  CheckCircle2, ChevronDown, CircleDot, Clock3, Code2, Command,
  FileJson, GitBranch, LayoutDashboard, ListChecks, LockKeyhole,
  RefreshCw, Search, ShieldCheck, TerminalSquare, Workflow, XCircle,
} from "lucide-react";
import {
  Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

type Step = {
  name: string; title?: string; status?: string; result?: string; summary?: string;
  started_at?: string; finished_at?: string; duration_ms?: number; attempt?: number;
  error?: { code?: string; message?: string } | null; input?: unknown; output?: unknown;
  artifacts?: { name?: string; path?: string; task_id?: string }[];
};
type Task = {
  task_id: string; carrier: string; biztype: string; status: string; problem: Record<string, unknown>;
  steps: Step[]; report: Record<string, unknown>; approval_pending: boolean;
};
type TaskSummary = { run_id: string; task_id: string; carrier: string; biztype: string; status: string; problem: Record<string, unknown>; steps_count: number; approval_pending: boolean };
type RunListItem = {
  run_id: string; status: string; created_at: string; updated_at: string;
  summary: { tasks_total: number; tasks_success: number; tasks_failed: number; tasks_manual: number; tasks_waiting_approval: number; steps_total: number };
  tasks?: TaskSummary[];
};
type Run = Omit<RunListItem, "tasks"> & { steps?: Step[]; tasks?: Task[] };
type Approval = { run_id: string; task_id: string; carrier: string; biztype: string; problem: Record<string, unknown>; updated_at: string };
type Artifact = { task_id: string; step: string; name: string; path: string };
type Page = "overview" | "runs" | "tasks" | "approvals" | "artifacts" | "system";

const nav: { id: Page; label: string; icon: typeof Activity }[] = [
  { id: "overview", label: "运行总览", icon: LayoutDashboard },
  { id: "runs", label: "执行记录", icon: Activity },
  { id: "tasks", label: "修复任务", icon: ListChecks },
  { id: "approvals", label: "人工审批", icon: ShieldCheck },
  { id: "artifacts", label: "产物与报告", icon: Archive },
  { id: "system", label: "系统诊断", icon: TerminalSquare },
];
const fmt = (v?: string | number) => {
  if (v === undefined || v === null || v === "") return "—";
  const date = typeof v === "number" ? new Date(v) : new Date(v);
  return Number.isNaN(date.getTime()) ? String(v) : date.toLocaleString();
};
const short = (v: string, n = 10) => v.length > n ? v.slice(0, n) + "…" : v;
const statusTone = (status = "") => {
  const s = status.toLowerCase();
  if (["success", "succeeded", "completed", "passed", "approve", "ok"].some(x => s.includes(x))) return "success";
  if (["failed", "fail", "reject", "error"].some(x => s.includes(x))) return "danger";
  if (["waiting", "pending", "manual", "running", "accepted"].some(x => s.includes(x))) return "warning";
  return "muted";
};
const pretty = (value: unknown) => JSON.stringify(value ?? {}, null, 2);

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const token = sessionStorage.getItem("workbench-token") || "";
  const response = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export default function App() {
  const [page, setPage] = useState<Page>("overview");
  const [runs, setRuns] = useState<RunListItem[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [selectedRun, setSelectedRun] = useState("");
  const [detail, setDetail] = useState<Run | null>(null);
  const [selectedTask, setSelectedTask] = useState("");
  const [selectedStep, setSelectedStep] = useState<Step | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [artifactContent, setArtifactContent] = useState<unknown>(null);
  const [health, setHealth] = useState<Record<string, unknown> | null>(null);
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [lastRefresh, setLastRefresh] = useState(new Date());
  const [tokenInput, setTokenInput] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [runIdInput, setRunIdInput] = useState("");

  const refresh = useCallback(async () => {
    try {
      setError("");
      const [runData, approvalData, healthData] = await Promise.all([
        api<{items: RunListItem[]}>("/api/runs?limit=100"),
        api<{items: Approval[]}>("/api/approvals"),
        api<Record<string, unknown>>("/api/health"),
      ]);
      setRuns(runData.items);
      setApprovals(approvalData.items);
      setHealth(healthData);
      setLastRefresh(new Date());
      if (selectedRun && runData.items.some(item => item.run_id === selectedRun)) {
        const current = await api<Run>(`/api/runs/${encodeURIComponent(selectedRun)}`);
        setDetail(current);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [selectedRun]);

  useEffect(() => { void refresh(); const id = window.setInterval(() => void refresh(), 5000); return () => window.clearInterval(id); }, [refresh]);
  useEffect(() => {
    if (!selectedRun) { setDetail(null); return; }
    let active = true;
    api<Run>(`/api/runs/${encodeURIComponent(selectedRun)}`)
      .then(data => { if (active) setDetail(data); })
      .catch(e => { if (active) setError(e instanceof Error ? e.message : String(e)); });
    return () => { active = false; };
  }, [selectedRun]);

  const allTasks = useMemo(() => runs.flatMap(run => (run.tasks || []).map(task => ({...task, run_id: run.run_id}))), [runs]);
  const filteredRuns = runs.filter(run => `${run.run_id} ${run.status}`.toLowerCase().includes(filter.toLowerCase()));
  const selectedTaskData = detail?.tasks?.find(task => task.task_id === selectedTask) || detail?.tasks?.[0] || null;
  const timeline = useMemo(() => {
    const steps = [...(detail?.steps || []), ...(detail?.tasks || []).flatMap(task => task.steps.map(step => ({...step, title: `${task.carrier || task.task_id} · ${step.title || step.name}` })))];
    return steps.sort((a, b) => String(a.started_at || "").localeCompare(String(b.started_at || "")));
  }, [detail]);

  async function openRun(id: string) {
    setSelectedRun(id); setSelectedTask(""); setSelectedStep(null); setArtifactContent(null);
    setPage("runs");
  }
  async function startRun() {
    setBusy(true);
    try {
      const result = await api<{run_id: string; message: string}>("/api/runs", { method: "POST", body: JSON.stringify({run_id: runIdInput.trim() || null}) });
      setRunIdInput(""); await refresh(); await openRun(result.run_id); setPage("runs");
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  async function decide(item: Approval, decision: "APPROVE" | "REJECT") {
    if (!window.confirm(`确认${decision === "APPROVE" ? "批准" : "拒绝"} ${item.carrier || item.task_id}？这将恢复并继续执行工作流。`)) return;
    setBusy(true);
    try {
      await api(`/api/runs/${encodeURIComponent(item.run_id)}/tasks/${encodeURIComponent(item.task_id)}/approval`, {method: "POST", body: JSON.stringify({decision})});
      await refresh(); await openRun(item.run_id);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  async function loadArtifacts(runId: string, taskId = "") {
    try {
      const result = await api<{items: Artifact[]}>(`/api/runs/${encodeURIComponent(runId)}/artifacts?task_id=${encodeURIComponent(taskId)}`);
      setArtifacts(result.items); setArtifactContent(null); setSelectedRun(runId); setSelectedTask(taskId); setPage("artifacts");
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }
  async function openArtifact(item: Artifact) {
    if (!selectedRun) return;
    try {
      const result = await api<{content: unknown}>(`/api/runs/${encodeURIComponent(selectedRun)}/artifacts/content?path=${encodeURIComponent(item.path)}&task_id=${encodeURIComponent(item.task_id || selectedTask)}`);
      setArtifactContent(result.content);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }
  function saveToken() { sessionStorage.setItem("workbench-token", tokenInput.trim()); setShowToken(false); void refresh(); }

  const counts = runs.reduce((acc, run) => {
    acc.runs += 1; acc.tasks += run.summary?.tasks_total || 0; acc.failed += run.summary?.tasks_failed || 0;
    acc.success += run.summary?.tasks_success || 0; acc.waiting += run.summary?.tasks_waiting_approval || 0;
    return acc;
  }, {runs: 0, tasks: 0, failed: 0, success: 0, waiting: approvals.length});
  const chartData = [...runs].slice(0, 12).reverse().map(run => ({
    run: short(run.run_id, 8), success: run.summary?.tasks_success || 0, failed: run.summary?.tasks_failed || 0,
  }));

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark"><Workflow size={22}/></div><div><b>RepairOps</b><small>AI WORKFLOW CONTROL</small></div></div>
      <div className="workspace-pill"><span className="live-dot"/><span>生产工作区</span><ChevronDown size={14}/></div>
      <div className="nav-label">工作空间</div>
      <nav>{nav.map(item => { const Icon = item.icon; return <button key={item.id} className={page === item.id ? "nav-item active" : "nav-item"} onClick={() => setPage(item.id)}><Icon size={17}/><span>{item.label}</span>{item.id === "approvals" && approvals.length > 0 && <i className="nav-count">{approvals.length}</i>}</button>; })}</nav>
      <div className="sidebar-bottom">
        <div className="secure-card"><LockKeyhole size={16}/><div><b>安全执行模式</b><small>交付需要通过门禁</small></div><span className="secure-dot"/></div>
        <button className="profile" onClick={() => setShowToken(true)}><div className="avatar">RO</div><div><b>运维控制台</b><small>{health?.api_token_required ? "Token 认证已启用" : "本地 API 模式"}</small></div><ChevronDown size={15}/></button>
      </div>
    </aside>
    <main className="main">
      <header className="topbar">
        <div className="breadcrumbs"><span>工作流平台</span><span className="slash">/</span><b>{nav.find(n => n.id === page)?.label}</b></div>
        <div className="top-actions"><span className="sync-status"><span className="live-dot"/>实时刷新 · 5 秒</span><button className="icon-button" title="刷新数据" onClick={() => void refresh()}><RefreshCw size={16}/></button><button className="outline-button" onClick={() => setShowToken(true)}><LockKeyhole size={15}/> API 认证</button><button className="primary-button" onClick={() => setPage("overview")}><Command size={15}/> 控制台</button></div>
      </header>
      {error && <div className="error-banner"><AlertTriangle size={17}/><span>{error}</span><button onClick={() => setError("")}><XCircle size={16}/></button></div>}
      <div className="page-content">
        <section className="page-heading"><div><div className="eyebrow">OBSERVABILITY / ORCHESTRATION</div><h1>{nav.find(n => n.id === page)?.label}</h1><p>{page === "overview" ? "统一观测执行状态、修复质量、节点耗时与人工决策。" : page === "runs" ? "查看每轮执行的状态、节点轨迹、输入输出与失败原因。" : page === "tasks" ? "从运输商问题到验证、回归、审查与交付，全链路追踪。" : page === "approvals" ? "审批是工作流状态迁移；决定后将从持久化检查点恢复执行。" : page === "artifacts" ? "定位执行产物、Agent 输出、差异与最终报告。" : "检查服务健康、配置完整性和工作台运行方式。"}</p></div>
          <div className="heading-actions"><span className="updated"><span className="live-dot"/>更新于 {lastRefresh.toLocaleTimeString()}</span>{page === "overview" && <button className="primary-button" onClick={() => setPage("runs")}><Activity size={15}/> 查看执行</button>}</div>
        </section>

        {page === "overview" && <>
          <div className="metric-grid">
            <Metric icon={<Activity/>} label="执行轮次" value={counts.runs} note="已落盘运行记录" tone="blue"/>
            <Metric icon={<ListChecks/>} label="修复任务" value={counts.tasks} note="按运输商任务统计" tone="purple"/>
            <Metric icon={<CheckCircle2/>} label="修复成功" value={counts.success} note={counts.tasks ? `成功率 ${Math.round(counts.success / counts.tasks * 100)}%` : "等待首轮执行"} tone="green"/>
            <Metric icon={<AlertTriangle/>} label="失败任务" value={counts.failed} note="需查看失败节点和产物" tone="red"/>
            <Metric icon={<ShieldCheck/>} label="待人工审批" value={approvals.length} note="审批后继续恢复工作流" tone="amber"/>
          </div>
          <div className="overview-grid">
            <section className="panel chart-panel"><PanelTitle title="执行结果趋势" subtitle="最近 12 轮执行 · 按任务结果汇总" action={<span className="legend"><i className="lg-green"/>成功 <i className="lg-red"/>失败</span>}/>{chartData.length ? <div className="chart"><ResponsiveContainer width="100%" height="100%"><AreaChart data={chartData}><defs><linearGradient id="okFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#23b26d" stopOpacity={0.22}/><stop offset="95%" stopColor="#23b26d" stopOpacity={0}/></linearGradient></defs><CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#edf0f5"/><XAxis dataKey="run" axisLine={false} tickLine={false} tick={{fill:"#8992a5",fontSize:11}}/><YAxis allowDecimals={false} axisLine={false} tickLine={false} tick={{fill:"#8992a5",fontSize:11}} width={28}/><Tooltip/><Area type="monotone" dataKey="success" name="成功" stroke="#23b26d" fill="url(#okFill)" strokeWidth={2.5}/><Area type="monotone" dataKey="failed" name="失败" stroke="#e05260" fill="transparent" strokeWidth={2.5}/></AreaChart></ResponsiveContainer></div> : <Empty title="暂无执行数据" body="启动工作流后，运行趋势将在这里展示。"/>}</section>
            <section className="panel"><PanelTitle title="待处理审批" subtitle="需要人工决策的任务" action={<button className="text-button" onClick={() => setPage("approvals")}>全部 <ArrowUpRight size={14}/></button>}/>{approvals.length ? <div className="approval-list">{approvals.slice(0,4).map(item => <div className="approval-mini" key={item.run_id + item.task_id}><div className="approval-icon"><ShieldCheck size={17}/></div><div className="approval-copy"><b>{item.carrier || item.task_id}</b><small>{item.biztype || "未知业务"} · {short(item.run_id,12)}</small></div><button className="icon-button" title="查看任务" onClick={() => void openRun(item.run_id)}><ArrowUpRight size={15}/></button></div>)}</div> : <Empty title="没有待处理审批" body="人工审批节点挂起时会自动进入队列。"/>}</section>
          </div>
          <section className="panel"><PanelTitle title="最近执行" subtitle="点击一轮执行查看完整节点时间线" action={<button className="text-button" onClick={() => setPage("runs")}>所有执行 <ArrowUpRight size={14}/></button>}/><RunTable runs={runs.slice(0,7)} onOpen={openRun}/></section>
        </>}

        {page === "runs" && <div className="runs-layout">
          <section className="panel run-list-panel"><div className="search-box"><Search size={16}/><input placeholder="搜索 Run ID / 状态" value={filter} onChange={e => setFilter(e.target.value)}/></div><RunTable runs={filteredRuns} onOpen={openRun} selected={selectedRun}/></section>
          <section className="run-detail-column">
            {detail ? <><section className="panel detail-summary"><div className="detail-top"><div><div className="eyebrow">EXECUTION ID</div><h2>{detail.run_id}</h2></div><Status status={detail.status}/></div><div className="detail-meta"><span><Clock3 size={14}/>创建 {fmt(detail.created_at)}</span><span><RefreshCw size={14}/>更新 {fmt(detail.updated_at)}</span></div><div className="detail-kpis"><MiniKpi label="任务总数" value={detail.summary?.tasks_total || 0}/><MiniKpi label="成功" value={detail.summary?.tasks_success || 0}/><MiniKpi label="失败" value={detail.summary?.tasks_failed || 0}/><MiniKpi label="待审批" value={detail.summary?.tasks_waiting_approval || 0}/></div></section>
              <section className="panel"><PanelTitle title="任务列表" subtitle="任务级状态、运输商和问题上下文"/>{detail.tasks?.length ? <div className="task-list">{detail.tasks.map(task => <button key={task.task_id} className={selectedTaskData?.task_id === task.task_id ? "task-row selected" : "task-row"} onClick={() => {setSelectedTask(task.task_id);setSelectedStep(null);}}><div className="task-icon"><Code2 size={17}/></div><div className="task-main"><b>{task.carrier || "未知运输商"}</b><small>{task.biztype || "未知业务"} · {short(task.task_id,30)}</small></div><Status status={task.approval_pending ? "WAITING_APPROVAL" : task.status}/></button>)}</div> : <Empty title="该轮暂无任务" body="查看 run 级节点以了解任务生成阶段。"/></section>
              {selectedTaskData && <section className="panel"><PanelTitle title="任务执行轨迹" subtitle={selectedTaskData.task_id} action={<button className="text-button" onClick={() => void loadArtifacts(detail.run_id, selectedTaskData.task_id)}>查看产物 <ArrowUpRight size={14}/></button>}/><div className="workflow-line">{selectedTaskData.steps.map((step,i) => <button className="workflow-step" key={step.name + i} onClick={() => setSelectedStep(step)}><div className={`step-marker ${statusTone(step.status || step.result)}`}>{statusTone(step.status || step.result) === "success" ? <Check size={12}/> : statusTone(step.status || step.result) === "danger" ? <XCircle size={12}/> : <CircleDot size={12}/>}</div><div className="step-content"><div className="step-heading"><b>{step.title || step.name}</b><span className="step-duration">{step.duration_ms != null ? `${Math.round(step.duration_ms)} ms` : step.status === "running" ? "执行中" : ""}</span></div><div className="step-meta"><span>{step.name}</span><Status status={step.result || step.status || "unknown"}/>{step.attempt && step.attempt > 1 && <small>第 {step.attempt} 次尝试</small>}</div>{step.summary && <p>{step.summary}</p>}{step.error?.message && <div className="step-error">{step.error.code}: {step.error.message}</div>}</div></button>)}</div></section>}
              {selectedStep && <section className="panel"><PanelTitle title={selectedStep.title || selectedStep.name} subtitle="节点输入、输出和错误上下文" action={<button className="icon-button" onClick={() => setSelectedStep(null)}><XCircle size={15}/></button>}/><div className="json-grid"><JsonPanel title="INPUT" value={selectedStep.input}/><JsonPanel title="OUTPUT" value={selectedStep.output}/></div>{selectedStep.error && <pre className="error-json">{pretty(selectedStep.error)}</pre>}</section>}
            </> : <section className="panel detail-placeholder"><Workflow size={34}/><h3>选择一轮执行</h3><p>查看节点时间线、任务结果、验证信息和交付产物。</p></section>}
          </section>
        </div>}

        {page === "tasks" && <section className="panel"><PanelTitle title="全局修复任务" subtitle="跨运行轮次汇总运输商问题与结果"/><div className="search-box task-search"><Search size={16}/><input placeholder="搜索运输商 / Task ID / Run ID" value={filter} onChange={e => setFilter(e.target.value)}/></div><div className="table-wrap"><table><thead><tr><th>运输商 / 任务</th><th>业务类型</th><th>运行轮次</th><th>状态</th><th>节点数</th><th>问题摘要</th><th/></tr></thead><tbody>{allTasks.filter(t => `${t.carrier} ${t.task_id} ${t.run_id} ${t.status}`.toLowerCase().includes(filter.toLowerCase())).map(task => <tr key={task.run_id + task.task_id}><td><b>{task.carrier || "—"}</b><small className="subcell">{short(task.task_id,22)}</small></td><td>{task.biztype || "—"}</td><td><button className="link-button" onClick={() => void openRun(task.run_id)}>{short(task.run_id,14)}</button></td><td><Status status={task.approval_pending ? "WAITING_APPROVAL" : task.status}/></td><td>{task.steps_count}</td><td className="problem-cell">{String(task.problem?.error || task.problem?.message || "—")}</td><td><button className="icon-button" onClick={() => {void openRun(task.run_id);setSelectedTask(task.task_id);}}><ArrowUpRight size={15}/></button></td></tr>)}</tbody></table></div></section>}

        {page === "approvals" && <section className="panel"><PanelTitle title="审批工作队列" subtitle="审批决定会通过 LangGraph 持久化检查点恢复流程"/>{approvals.length ? <div className="approval-table">{approvals.map(item => <div className="approval-card" key={item.run_id + item.task_id}><div className="approval-card-head"><div className="approval-icon large"><ShieldCheck size={20}/></div><div className="approval-copy"><b>{item.carrier || item.task_id}</b><small>{item.biztype || "未知业务"} · Run {short(item.run_id,18)}</small></div><Status status="WAITING_APPROVAL"/></div><div className="problem-box"><span>问题 / 审批上下文</span><p>{String(item.problem?.error || item.problem?.message || "工作流请求人工确认。请先打开任务详情检查差异、验证结果与审查意见。")}</p></div><div className="approval-footer"><button className="outline-button" onClick={() => void openRun(item.run_id)}>查看执行证据</button><div className="button-group"><button disabled={busy} className="reject-button" onClick={() => void decide(item,"REJECT")}><XCircle size={15}/> 拒绝</button><button disabled={busy} className="approve-button" onClick={() => void decide(item,"APPROVE")}><CheckCircle2 size={15}/> 批准并恢复</button></div></div></div>)}</div> : <Empty title="审批队列为空" body="没有发现等待人工决策的任务。审批节点挂起后会显示在这里。"/>}</section>}

        {page === "artifacts" && <div className="artifact-layout"><section className="panel artifact-list"><PanelTitle title="执行产物" subtitle="选择一个执行后列出该轮可见产物"/><div className="field-label">运行轮次</div><select value={selectedRun} onChange={e => {setSelectedRun(e.target.value);void loadArtifacts(e.target.value,selectedTask);}}><option value="">选择 Run ID</option>{runs.map(run => <option value={run.run_id} key={run.run_id}>{run.run_id}</option>)}</select>{selectedRun && <div className="artifact-items">{artifacts.map((item,i) => <button key={item.path + i} className="artifact-item" onClick={() => void openArtifact(item)}><FileJson size={17}/><span><b>{item.name || item.path}</b><small>{item.step} · {item.path}</small></span><ArrowUpRight size={14}/></button>)}</div>}{selectedRun && artifacts.length === 0 && <Empty title="未找到引用产物" body="请检查节点记录或选择具体任务。"/>}</section><section className="panel artifact-preview"><PanelTitle title="产物预览" subtitle="JSON、报告与文本内容"/>{artifactContent !== null ? <pre className="code-preview">{pretty(artifactContent)}</pre> : <Empty title="选择一个产物" body="产物内容将在这里展示；敏感工作区文件不会被自动暴露。"/>}</section></div>}

        {page === "system" && <div className="system-grid"><section className="panel"><PanelTitle title="服务健康" subtitle="API、运行目录和关键配置"/><div className="health-row"><span>工作台 API</span><Status status={String(health?.status || "unknown")}/></div><div className="health-row"><span>运行目录配置</span><Status status={health?.runtime_dir_configured ? "configured" : "missing"}/></div><div className="health-row"><span>源码仓库配置</span><Status status={health?.source_repo_configured ? "configured" : "missing"}/></div><div className="health-row"><span>API Token 保护</span><Status status={health?.api_token_required ? "enabled" : "local-only"}/></div><div className="health-row"><span>API 版本</span><code>{String(health?.version || "—")}</code></div><div className="health-row"><span>最近检测</span><span>{fmt(String(health?.timestamp || ""))}</span></div></section><section className="panel"><PanelTitle title="运维注意事项" subtitle="生产环境上线前必须落实"/><div className="system-note"><ShieldCheck size={18}/><div><b>访问控制</b><p>远程部署设置 WORKBENCH_API_TOKEN，并通过 HTTPS 反向代理；不要把 API 直接暴露到公网。</p></div></div><div className="system-note"><Archive size={18}/><div><b>持久化与备份</b><p>备份运行记录目录、SQLite checkpoint 与资格数据库；将 runtime 目录放在持久化磁盘。</p></div></div><div className="system-note"><GitBranch size={18}/><div><b>交付门禁</b><p>工作台不会自动合并修复分支。审批、验证、回归、审查证据应先检查再执行交付。</p></div></div><div className="system-note"><AlertTriangle size={18}/><div><b>运行诊断</b><p>本工作台展示已落盘的记录；如果进程在首个步骤落盘前崩溃，需结合服务进程日志排查。</p></div></div></section><section className="panel"><PanelTitle title="API 端点" subtitle="可供自动化监控和集成使用"/>{["GET /api/health","GET /api/runs","GET /api/runs/{run_id}","GET /api/approvals","GET /api/runs/{run_id}/artifacts","POST /api/runs","POST /api/runs/{run_id}/tasks/{task_id}/approval","GET /api/docs"].map(path => <div className="endpoint" key={path}><code>{path}</code><button className="icon-button" title="复制端点" onClick={() => void navigator.clipboard.writeText(path)}><ArrowDownToLine size={14}/></button></div>)}</section></div>}
      </div>
      <footer className="footer"><span>RepairOps · AI 异常日志自动修复工作流</span><span><span className="live-dot"/> API {health?.status === "ok" ? "Operational" : "Checking"} · 刷新间隔 5s</span></footer>
    </main>
    {showToken && <div className="modal-backdrop" onClick={() => setShowToken(false)}><section className="modal" onClick={e => e.stopPropagation()}><div className="modal-heading"><div><h3>API 认证配置</h3><p>仅在当前浏览器会话保存 Token，不写入源码或持久化存储。</p></div><button className="icon-button" onClick={() => setShowToken(false)}><XCircle size={17}/></button></div><label className="field-label">WORKBENCH_API_TOKEN</label><input className="token-input" type="password" value={tokenInput} onChange={e => setTokenInput(e.target.value)} placeholder="粘贴后端环境变量对应的 Token"/><div className="modal-actions"><button className="outline-button" onClick={() => {sessionStorage.removeItem("workbench-token");setTokenInput("");setShowToken(false);void refresh();}}>清除 Token</button><button className="primary-button" onClick={saveToken}>保存并验证</button></div></section></div>}
  </div>;
}

function Metric({icon,label,value,note,tone}:{icon:ReactNode;label:string;value:number;note:string;tone:string}) {
  return <div className="metric-card"><div className="metric-top"><span>{label}</span><span className={`metric-icon ${tone}`}>{icon}</span></div><div className="metric-value">{value.toLocaleString()}</div><div className="metric-note">{note}</div></div>;
}
function PanelTitle({title,subtitle,action}:{title:string;subtitle?:string;action?:ReactNode}) {
  return <div className="panel-title"><div><h3>{title}</h3>{subtitle && <p>{subtitle}</p>}</div>{action}</div>;
}
function Status({status}:{status:string}) {
  const tone = statusTone(status);
  return <span className={`status ${tone}`}><i/>{status.replaceAll("_"," ") || "unknown"}</span>;
}
function MiniKpi({label,value}:{label:string;value:number}) { return <div><span>{label}</span><b>{value}</b></div>; }
function Empty({title,body}:{title:string;body:string}) { return <div className="empty-state"><div className="empty-icon"><Archive size={19}/></div><b>{title}</b><p>{body}</p></div>; }
function JsonPanel({title,value}:{title:string;value:unknown}) { return <div className="json-panel"><div>{title}</div><pre>{pretty(value)}</pre></div>; }
function RunTable({runs,onOpen,selected=""}:{runs:RunListItem[];onOpen:(id:string)=>void;selected?:string}) {
  return <div className="table-wrap"><table><thead><tr><th>执行轮次</th><th>状态</th><th>任务</th><th>成功 / 失败</th><th>最近更新</th><th/></tr></thead><tbody>{runs.map(run => <tr key={run.run_id} className={selected === run.run_id ? "row-selected" : ""} onClick={() => onOpen(run.run_id)}><td><div className="run-id"><div className="run-id-icon"><Workflow size={15}/></div><span><b>{short(run.run_id,26)}</b><small>{short(run.run_id,12)}</small></span></div></td><td><Status status={run.status}/></td><td>{run.summary?.tasks_total ?? 0}</td><td><span className="success-number">{run.summary?.tasks_success || 0}</span><span className="cell-separator">/</span><span className="danger-number">{run.summary?.tasks_failed || 0}</span></td><td className="time-cell">{fmt(run.updated_at)}</td><td><button className="icon-button" title="打开执行" onClick={e => {e.stopPropagation();onOpen(run.run_id);}}><ArrowUpRight size={15}/></button></td></tr>)}</tbody></table>{runs.length === 0 && <Empty title="没有运行记录" body="启动工作流后，这里会显示每轮执行。"/>}</div>;
}
