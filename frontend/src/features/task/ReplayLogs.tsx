import { useState } from 'react';
import { Button, Drawer, Radio, Tabs, message } from 'antd';
import { DownloadOutlined, FileTextOutlined } from '@ant-design/icons';
import { api } from '../../lib/api';
import { object, readableStdout, ReplayComparison, saveEvidence, TextEvidence } from './ReplayEvidence';

export function ReplayLogs({ replay, runId, taskId, artifactPath }: {
  replay: Record<string, unknown>; runId: string; taskId: string; artifactPath?: string;
}) {
  const [open, setOpen] = useState(false);
  const [side, setSide] = useState('fixed');
  const recipe = object(replay.replay_recipe);
  const input = object(recipe.input);
  const prefix = side === 'baseline' ? 'replay_baseline_' : 'replay_';
  const stdout = String(replay[`${prefix}stdout`] || '');
  const displayed = readableStdout(stdout, String(replay[`${prefix}output`] || ''));
  const channels = [
    { name: '运行日志', key: 'stdout', raw: stdout, view: displayed.text },
    { name: '标准错误', key: 'stderr', raw: String(replay[`${prefix}stderr`] || ''), view: undefined },
    { name: '执行异常', key: 'error', raw: String(replay[`${prefix}error`] || ''), view: undefined },
    { name: '异常堆栈', key: 'traceback', raw: String(replay[`${prefix}traceback`] || ''), view: undefined },
  ].filter((channel) => channel.raw.trim());
  const formatCommand = (value: unknown) => Array.isArray(value)
    ? '& ' + value.map((item) => "'" + String(item).replaceAll("'", "''") + "'").join(' ') : String(value || '');
  const technical = [
    { key: 'command', label: '历史命令原文', text: formatCommand(replay[`${prefix}command`]) },
    { key: 'runner', label: '平台执行脚本', text: String(recipe.entry_script || '') },
    { key: 'adapter', label: '解析输入适配器', text: String(recipe.runtime_adapter || '') },
    { key: 'model', label: '输入构造器', text: String(recipe.model_builder || '') },
  ].filter((item) => item.text.trim());
  const download = async () => {
    try { saveEvidence(artifactPath ? await api.artifactDownload(runId, taskId, artifactPath) : new Blob([JSON.stringify(replay, null, 2)], { type: 'application/json' }), 'verification_report.json'); }
    catch (error) { void message.error(error instanceof Error ? error.message : '下载失败'); }
  };
  return <div className="replay-log-entry">
    <div><strong>验证证据</strong><span>{replay.replay_full_output === true ? '原始输入、实际返回及执行记录' : '历史归档可能不完整，需重跑补齐'}</span></div>
    <Button icon={<FileTextOutlined />} onClick={() => setOpen(true)}>查看验证证据</Button>
    <Button icon={<DownloadOutlined />} onClick={() => void download()}>下载完整报告</Button>
    <Drawer className="replay-log-drawer" title="验证证据" open={open} onClose={() => setOpen(false)} width="min(1160px, 96vw)" styles={{ body: { padding: 0, overflow: 'auto' } }}>
      <Tabs defaultActiveKey="comparison" items={[
        { key: 'comparison', label: '前后对照', children: <div className="replay-evidence-pane"><p>{String(object(replay.verification).goal || '对照原样本检查实际行为变化')}</p><ReplayComparison replay={replay} full /><p className="evidence-note">历史判定：{String(replay.reason || '未记录')}。非 Succeed 返回需结合具体原因判断，不能一律解释为同一种失败。</p></div> },
        { key: 'response', label: '原始响应', children: input.response ? <TextEvidence text={String(input.response)} name="recorded-response.txt" /> : <p className="evidence-note">该记录没有归档原始响应，需补充后重新验证。</p> },
        { key: 'execution', label: '执行详情', children: <div className="replay-evidence-pane">
          <Radio.Group aria-label="选择执行版本" value={side} onChange={(event) => setSide(event.target.value)} optionType="button" options={[{ label: '修复前', value: 'baseline' }, { label: '修复后', value: 'fixed' }]} />
          <p className="evidence-note">进程退出码：{String(replay[`${prefix}returncode`] ?? '未记录')}。退出码 0 只表示脚本执行完成。</p>
          {channels.length ? channels.map((channel) => <details className="evidence-technical" key={`${side}-${channel.key}`} open={channel.key === 'stdout'}><summary>{channel.name}</summary><TextEvidence key={`${side}-${channel.key}-text`} text={channel.raw} displayText={channel.view} name={`${side}-${channel.key}.txt`} notice={channel.key === 'stdout' && displayed.separated ? '重复打印的结构化结果已移至“前后对照”；下载和复制原文仍包含完整输出。' : undefined} /></details>) : <p>没有记录运行日志。</p>}
          {technical.length > 0 && <details className="evidence-technical"><summary>技术材料 · 历史命令与验证脚本</summary><p className="evidence-note">历史命令的临时路径可能已清理，不能直接复制重跑。完整报告保留重建所需输入及脚本。</p>{technical.map((item) => <details key={item.key}><summary>{item.label}</summary><TextEvidence text={item.text} name={`${side}-${item.key}.txt`} /></details>)}</details>}
        </div> },
      ]} />
    </Drawer>
  </div>;
}
