import { Button, Result } from 'antd';
import { useNavigate } from 'react-router-dom';
export function NotFoundPage() {
  const navigate = useNavigate();
  return <Result status="404" title="页面不存在" subTitle="页面可能已移动，或当前地址无效。" extra={<Button type="primary" onClick={() => navigate('/dashboard')}>返回总览</Button>} />;
}