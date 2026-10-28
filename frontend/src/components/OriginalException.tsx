import { App, Button, Skeleton } from 'antd';
import { CopyOutlined } from '@ant-design/icons';
import styles from './OriginalException.module.css';

/** 所有原始异常区域只展示完整 message，保留换行、空格和结尾。 */
export function OriginalException({ message: original, loading, failed }: {
  message: string;
  loading?: boolean;
  failed?: boolean;
}) {
  const { message } = App.useApp();
  if (loading && !original) return <Skeleton active title={false} paragraph={{ rows: 3 }} />;
  if (!original) return <p className={styles.empty}>{failed
    ? '原始异常 message 读取失败，请刷新后重试。'
    : '这条历史记录未保存完整 message，无法还原原始异常全文。'}</p>;
  return <div className={styles.root}>
    <div className={styles.toolbar}>
      <span>MESSAGE <span>· {original.length.toLocaleString()} 字符</span></span>
      <Button size="small" type="text" icon={<CopyOutlined />} onClick={() => void navigator.clipboard.writeText(original)
        .then(() => message.success('已复制完整 message')).catch(() => message.error('复制失败，请手动选择原文'))}>复制全文</Button>
    </div>
    <pre className={styles.message} tabIndex={0} aria-label="完整异常 message">{original}</pre>
  </div>;
}
