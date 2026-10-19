import { useMemo, useState } from 'react';
import { Button, Descriptions, Empty, Space, Table, Tag, Typography } from 'antd';

const { Text } = Typography;

function displayScalar(value: unknown): string {
  if (value === null) return 'null';
  if (value === undefined) return '—';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'string') return value.length ? value : '（空字符串）';
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : '—';
  return String(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function Scalar({ value }: { value: unknown }) {
  if (typeof value === 'boolean') return <Tag color={value ? 'green' : 'default'}>{value ? '是' : '否'}</Tag>;
  if (value === null || value === undefined || value === '') return <Text type="secondary">{displayScalar(value)}</Text>;
  if (typeof value === 'string' && value.length > 500) return <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>{value}</Typography.Paragraph>;
  return <span style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{displayScalar(value)}</span>;
}

function ArrayView({ value, depth }: { value: unknown[]; depth: number }) {
  if (!value.length) return <Text type="secondary">空列表</Text>;
  if (value.every(isRecord)) {
    const keys = Array.from(new Set(value.flatMap((item) => Object.keys(item))));
    const columns = keys.slice(0, 8).map((key) => ({
      title: key,
      dataIndex: key,
      key,
      render: (cell: unknown) => isRecord(cell) || Array.isArray(cell)
        ? <StructuredValue value={cell} depth={depth + 1} />
        : <Scalar value={cell} />,
    }));
    return <Table size="small" rowKey={(_, index) => String(index)} columns={columns} dataSource={value} pagination={{ pageSize: 5, size: 'small', hideOnSinglePage: true }} scroll={{ x: true }} />;
  }
  return <div style={{ display: 'grid', gap: 8 }}>{value.map((item, index) => <div key={index} style={{ display: 'grid', gridTemplateColumns: 'minmax(60px, 100px) minmax(0, 1fr)', gap: 12, borderBottom: '1px solid #f0f0f0', paddingBottom: 8 }}><Text type="secondary">[{index}]</Text><div>{isRecord(item) || Array.isArray(item) ? <StructuredValue value={item} depth={depth + 1} /> : <Scalar value={item} />}</div></div>)}</div>;
}

export function StructuredValue({ value, depth = 0 }: { value: unknown; depth?: number }) {
  const [raw, setRaw] = useState(false);
  const formatted = useMemo(() => {
    try { return JSON.stringify(value, null, 2) ?? 'null'; } catch { return String(value); }
  }, [value]);

  if (raw) return <div className="structured-value-raw">
    <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>
      <Button size="small" onClick={() => setRaw(false)}>返回字段视图</Button>
      <Button size="small" onClick={() => void navigator.clipboard?.writeText(formatted)}>复制 JSON</Button>
    </div>
    <pre className="json-panel">{formatted}</pre>
  </div>;

  if (value === null || value === undefined) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无数据" />;
  if (Array.isArray(value)) return <div className="structured-value">
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}><Text type="secondary">{value.length} 项</Text><Button size="small" onClick={() => setRaw(true)}>原始 JSON</Button></div>
    <ArrayView value={value} depth={depth} />
  </div>;
  if (!isRecord(value)) return <div className="structured-value"><div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}><Button size="small" onClick={() => setRaw(true)}>原始 JSON</Button></div><Scalar value={value} /></div>;

  const entries = Object.entries(value);
  if (!entries.length) return <div><Text type="secondary">空对象</Text><Button size="small" type="link" onClick={() => setRaw(true)}>原始 JSON</Button></div>;
  return <div className="structured-value">
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}><Text type="secondary">{entries.length} 个字段</Text><Space><Button size="small" onClick={() => void navigator.clipboard?.writeText(formatted)}>复制 JSON</Button><Button size="small" onClick={() => setRaw(true)}>原始 JSON</Button></Space></div>
    <Descriptions size="small" bordered column={1} labelStyle={{ width: depth > 0 ? 150 : 190, fontWeight: 500, verticalAlign: 'top' }} contentStyle={{ overflowWrap: 'anywhere' }}>
      {entries.map(([key, child]) => <Descriptions.Item key={key} label={key}>
        {isRecord(child) || Array.isArray(child) ? <StructuredValue value={child} depth={depth + 1} /> : <Scalar value={child} />}
      </Descriptions.Item>)}
    </Descriptions>
  </div>;
}
