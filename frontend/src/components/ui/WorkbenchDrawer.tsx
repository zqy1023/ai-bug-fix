import { Button, Drawer, type DrawerProps } from 'antd';
import type { ReactNode } from 'react';
import styles from './WorkbenchDrawer.module.css';

export interface WorkbenchDrawerProps extends Omit<DrawerProps, 'title'> {
  readonly title: ReactNode;
}

/** Shared read-only evidence surface. Keep operational decisions in their own dialogs. */
export function WorkbenchDrawer({ title, children, className, footer, ...props }: WorkbenchDrawerProps) {
  return <Drawer {...props} title={title} className={`${styles.drawer}${className ? ` ${className}` : ''}`}
    footer={footer ?? <div className={styles.footer}><span>只读执行证据</span><Button onClick={props.onClose}>关闭</Button></div>}>
    {children}
  </Drawer>;
}
