import { Link } from 'react-router-dom';
import { Card, Empty } from '../components/ui';

export function NotFoundPage() {
  return (
    <Card>
      <Empty
        title="页面不存在"
        desc="这条路径没有对应的页面。"
        icon="404"
        action={<Link className="btn primary" to="/">回到概览</Link>}
      />
    </Card>
  );
}
