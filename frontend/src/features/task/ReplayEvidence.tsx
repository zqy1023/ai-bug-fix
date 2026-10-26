import { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Input, message } from 'antd';
import styles from './ReplayEvidence.module.css';

export function object(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export function saveEvidence(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url; anchor.download = name; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function cleanTerminal(text: string): string {
  return text.replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '');
}

export function evidenceWarning(replay: Record<string, unknown>): string {
  const comparison = object(replay.replay_comparison);
  const calls = ['baseline', 'fixed'].flatMap((side) => {
    const value = object(object(comparison[side]).observation).parser_calls;
    return Array.isArray(value) ? value.map(object) : [];
  });
  if (calls.some((call) => String(call.entrypoint || '').includes('traced_process'))) {
    return '旧记录把平台包装函数当作实际解析入口，输入送达证据无效；需重跑验证，不能据此判断补丁效果。';
  }
  if (replay.replay_comparison && Number(object(replay.verification).schema_version || 0) < 3) {
    return '这是旧版验证证据，需重跑以确认真实解析入口；历史判定保留供追溯。';
  }
  if (replay.replay_comparison && Number(object(replay.verification).schema_version || 0) < 4) {
    return '这是升级前的业务验证记录，未按当前规则核对框架业务错误；原结论保留，批准前需重新验证。';
  }
  return '';
}

export function shortError(value: unknown): string {
  const text = cleanTerminal(String(value || '')).trim();
  const firstLine = text.split(/\r?\n/)[0];
  return firstLine.length > 180 ? `${firstLine.slice(0, 180)}…` : firstLine;
}

export function TextEvidence({ text, name, displayText, notice }: {
  text: string; name: string; displayText?: string; notice?: string;
}) {
  const [wrap, setWrap] = useState(true);
  const [search, setSearch] = useState('');
  const [cursor, setCursor] = useState(0);
  const match = useRef<HTMLElement>(null);
  const visible = cleanTerminal(displayText ?? text);
  const needle = search.toLocaleLowerCase();
  const at = needle ? visible.toLocaleLowerCase().indexOf(needle, cursor) : -1;
  useEffect(() => { match.current?.scrollIntoView({ block: 'nearest' }); }, [at, search]);
  return <div className={styles.textEvidence}>
    <div className={styles.toolbar}>
      <span>原文 {text.length.toLocaleString()} 字符</span>
      <Input aria-label="查找当前证据" placeholder="查找文本" value={search} onChange={(event) => { setSearch(event.target.value); setCursor(0); }} />
      {search && <Button size="small" disabled={at < 0} onClick={() => setCursor(visible.toLocaleLowerCase().indexOf(needle, at + needle.length) < 0 ? 0 : at + needle.length)}>下一处</Button>}
      <Checkbox checked={wrap} onChange={(event) => setWrap(event.target.checked)}>自动换行</Checkbox>
      <Button size="small" onClick={() => void navigator.clipboard.writeText(text).then(() => message.success('已复制完整原文')).catch(() => message.error('复制失败'))}>复制原文</Button>
      <Button size="small" onClick={() => saveEvidence(new Blob([text], { type: 'text/plain;charset=utf-8' }), name)}>下载原文</Button>
    </div>
    {notice && <p className={styles.note}>{notice}</p>}
    {search && <p className={styles.note}>{at < 0 ? '未找到匹配文本' : `匹配位置：第 ${at + 1} 个字符`}</p>}
    <pre className={`${styles.code}${wrap ? ` ${styles.wrapped}` : ''}`}>{at < 0 ? visible || '没有记录内容' : <>{visible.slice(0, at)}<mark ref={match}>{visible.slice(at, at + search.length)}</mark>{visible.slice(at + search.length)}</>}</pre>
  </div>;
}

export function ReplayComparison({ replay, full = false }: { replay: Record<string, unknown>; full?: boolean }) {
  const comparison = object(replay.replay_comparison);
  const warning = evidenceWarning(replay);
  return <>
    {warning && <p className={styles.warning} role="alert">{warning}</p>}
    <div className={styles.resultGrid}>
      {(['baseline', 'fixed'] as const).map((side) => {
        const value = object(comparison[side]);
        const observation = object(value.observation);
        const calls = Array.isArray(observation.parser_calls) ? observation.parser_calls.map(object) : [];
        const errors = Array.isArray(value.parse_errors) ? value.parse_errors.map(object) : [];
        const businessErrors = Array.isArray(value.business_errors) ? value.business_errors.map(object) : [];
        const inputVerified = observation.schema_version === 3 && observation.input_delivered === true && calls.length > 0
          && calls.every((call) => call.business_entry === true && !String(call.entrypoint || '').includes('traced_process'));
        let result: unknown;
        try { result = object(JSON.parse(String(replay[side === 'baseline' ? 'replay_baseline_output' : 'replay_output'] || '{}'))).result; } catch { result = undefined; }
        return <article className={styles.resultCard} key={side}>
          <strong>{side === 'baseline' ? '修复前' : '修复后'}</strong>
          <p>框架返回：<b>{String(value.error_type || '未记录')}</b></p>
          <p>原因：{shortError(value.error_msg) || '未返回原因'}</p>
          {businessErrors.map((error, index) => <p key={`business-${index}`} role="alert">框架业务错误：<b>{String(error.category || '未分类')}</b> · {shortError(error.message)}</p>)}
          {errors.map((error, index) => {
            const frames = Array.isArray(error.frames) ? error.frames.map(object) : [];
            const frame = frames.at(-1);
            return <p key={index}>解析原异常：<b>{String(error.type || '未知类型')}</b> · {shortError(error.message)}{frame && <small className={styles.location}>{String(frame.file || '').replaceAll('\\', '/').split('/').slice(-3).join('/')}:{String(frame.line || '')} · {String(frame.function || '')}</small>}</p>;
          })}
          <p>输入送达：{inputVerified ? '已确认同一正文到达业务解析入口' : '未确认'}</p>
          {inputVerified && <p>实际解析函数：<code>{calls.map((call) => String(call.entrypoint || '')).join('、')}</code></p>}
          <p>轨迹数量：{String(value.event_count ?? '未记录')} · 执行：{value.executed === true ? '已完成' : '未完成或未记录'}</p>
          {full && result !== undefined && <><pre className={styles.resultPreview}>{JSON.stringify(result, null, 2)}</pre><details className={styles.technical}><summary>完整框架结果</summary><TextEvidence text={JSON.stringify(result, null, 2)} name={`${side}-result.json`} /></details></>}
        </article>;
      })}
    </div>
  </>;
}

export function readableStdout(stdout: string, output: string): { text: string; separated: boolean } {
  let result: Record<string, unknown>;
  try { result = object(JSON.parse(output)); } catch { return { text: stdout, separated: false }; }
  let separated = false;
  const lines = stdout.split('\n').filter((line) => {
    try {
      const candidate = object(JSON.parse(cleanTerminal(line)));
      if (candidate.success === true && result.success === true && candidate.result &&
        JSON.stringify(candidate.result) === JSON.stringify(result.result) && candidate.response === result.response) {
        separated = true;
        return false;
      }
    } catch { /* 普通日志逐行保留；只分离与归档完全一致的结构化结果帧。 */ }
    return true;
  });
  return { text: lines.join('\n'), separated };
}
