import { useNavigate } from 'react-router-dom';
import { Tag } from 'antd';
import { FileTextOutlined } from '@ant-design/icons';
import type { ApprovalItem } from '../../lib/types';
import { formatTime } from '../../lib/format';

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="review-fact">
      <span>{label}</span>
      <div>{children}</div>
    </div>
  );
}

/** 审阅面板：摘要、方案范围与变更文件清单，只呈现后端已有事实。 */
export function ReviewPane({ item }: { item: ApprovalItem }) {
  const navigate = useNavigate();
  const files = item.changed_files ?? [];
  const edits = item.change_plan?.edits ?? [];
  const editOf = (path: string) => edits.find((edit) => edit.path === path);

  return (
    <article className="review-pane">
      <header className="review-head">
        <div>
          <span className="review-kicker">交付审批 · {item.approval_id}</span>
          <h2>{item.carrier || '未识别运输商'}</h2>
          <p>{item.summary || '后端未提供摘要，请结合变更文件与方案说明判断。'}</p>
        </div>
        <Tag color={item.scope === 'carrier' ? 'blue' : 'orange'}>
          {item.scope === 'carrier' ? '运输商范围' : '项目 / 共享范围'}
        </Tag>
      </header>

      <div className="review-facts">
        <Fact label="执行">
          <button
            type="button"
            className="link-button"
            onClick={() => navigate(`/runs/${encodeURIComponent(item.run_id)}`)}
          >
            run:{item.run_id}
          </button>
        </Fact>
        <Fact label="任务">
          <button
            type="button"
            className="link-button"
            onClick={() => navigate(`/runs/${encodeURIComponent(item.run_id)}/tasks/${encodeURIComponent(item.task_id)}`)}
          >
            {item.task_id}
          </button>
        </Fact>
        <Fact label="申请时间">{formatTime(item.created_at)}</Fact>
        <Fact label="审批到期">{item.expires_at ? formatTime(item.expires_at) : '未设置'}</Fact>
      </div>

      <section className="review-section">
        <h3>方案说明</h3>
        <p className="review-rationale">
          {item.change_plan?.rationale || '未提供额外方案说明；请以变更文件清单为准。'}
        </p>
        {edits.length > 0 && (
          <ul className="review-edit-list">
            {edits.map((edit) => (
              <li key={`${edit.path}-${edit.action}`}>
                <code>{edit.path}</code>
                <Tag>{edit.action}</Tag>
                <span>{edit.note || '—'}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="review-section">
        <h3>变更文件 <span className="review-count">{files.length}</span></h3>
        {files.length ? (
          <ul className="review-file-list">
            {files.map((path) => {
              const edit = editOf(path);
              return (
                <li key={path}>
                  <span className="review-file-icon"><FileTextOutlined /></span>
                  <code>{path}</code>
                  {edit && <Tag>{edit.action}</Tag>}
                  <span className="review-file-note">{edit?.note || '交付分支中的已修改文件'}</span>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="review-empty">后端未返回变更文件列表，请先进入任务工作区确认交付产物。</p>
        )}
      </section>

      <p className="review-caution">
        批准只保留交付分支供人工合并，不会自动合入目标分支；驳回会删除该分支并回到修复链路。
      </p>
    </article>
  );
}
