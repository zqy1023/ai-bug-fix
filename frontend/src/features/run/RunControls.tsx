import { App, Button, Tooltip } from 'antd';
import { CaretRightOutlined, PauseOutlined, ReloadOutlined, StopOutlined } from '@ant-design/icons';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import type { RunDetail } from '../../lib/types';
import styles from './RunControls.module.css';

export function RunControls({ run, refresh }: { run: RunDetail; refresh: () => void }) {
  const { message, modal } = App.useApp();
  const client = useQueryClient();
  const control = useMutation({
    mutationFn: (action: 'retry' | 'resume' | 'pause' | 'cancel') => ({
      retry: api.retryRun, resume: api.resumeRun, pause: api.pauseRun, cancel: api.cancelRun,
    })[action](run.run_id),
    onSuccess: async (result) => {
      message.success(result.message || '操作已受理，请查看执行状态');
      await Promise.all([
        client.invalidateQueries({ queryKey: queryKeys.run(run.run_id) }),
        client.invalidateQueries({ queryKey: queryKeys.board(run.run_id) }),
        client.invalidateQueries({ queryKey: queryKeys.dashboard }),
        client.invalidateQueries({ queryKey: queryKeys.runsRoot }),
      ]);
    },
    onError: (error) => message.error(error instanceof Error ? error.message : '操作失败'),
  });
  return <div className={styles.actions}>
    {run.actions?.retry.enabled && <Button type="primary" icon={<ReloadOutlined />} loading={control.isPending} onClick={() => control.mutate('retry')}>重试执行</Button>}
    {run.actions?.resume.enabled && <Button type="primary" icon={<CaretRightOutlined />} loading={control.isPending} onClick={() => control.mutate('resume')}>恢复执行</Button>}
    {run.actions?.pause.enabled && <Button icon={<PauseOutlined />} loading={control.isPending} onClick={() => control.mutate('pause')}>暂停</Button>}
    {run.actions?.cancel.enabled && <Button danger icon={<StopOutlined />} onClick={() => modal.confirm({
      title: '停止本轮执行？', content: '将在当前节点的安全边界停止，已产出的记录保留。',
      okText: '停止执行', cancelText: '返回', onOk: () => control.mutateAsync('cancel'),
    })}>停止</Button>}
    <Tooltip title="重新读取执行状态"><Button aria-label="刷新执行详情" icon={<ReloadOutlined />} onClick={refresh} /></Tooltip>
  </div>;
}
