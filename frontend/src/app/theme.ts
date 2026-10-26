import { theme } from 'antd';
import type { ThemeConfig } from 'antd';

import { fontFamily, semantic, type ThemeMode } from './tokens';
export { palette } from './tokens';

/** 结果分类色调:Task 是最高优先级观察对象,颜色只做辅助编码。 */
export type Tone = 'running' | 'waiting' | 'success' | 'danger' | 'manual' | 'neutral' | 'pending';

/** tone → 语义令牌键(StatusDot / StatusBadge 共用)。 */
export const toneVar: Record<Tone, string> = {
  running: 'primary',
  waiting: 'warning',
  success: 'success',
  danger: 'danger',
  manual: 'ink-faint',
  neutral: 'ink-faint',
  pending: 'ink-faint',
};

/** tone → AntD 图标色(少数必须内联色值的场景)。 */
export const toneColor: Record<Tone, string> = {
  running: semantic.light.primary,
  waiting: semantic.light.warning,
  success: semantic.light.success,
  danger: semantic.light.danger,
  manual: semantic.light.inkFaint,
  neutral: semantic.light.inkFaint,
  pending: semantic.light.inkFaint,
};

export function getAntdTheme(mode: ThemeMode): ThemeConfig {
  const s = semantic[mode];
  return {
    algorithm: mode === 'dark' ? theme.darkAlgorithm : theme.defaultAlgorithm,
    token: {
      colorPrimary: s.accent,
      colorInfo: s.accent,
      colorSuccess: s.success,
      colorWarning: s.warning,
      colorError: s.danger,
      colorTextBase: s.ink,
      colorText: s.ink,
      colorTextSecondary: s.inkMuted,
      colorTextTertiary: s.inkFaint,
      colorBgLayout: s.canvas,
      colorBgContainer: s.surface,
      colorBorder: s.line,
      colorBorderSecondary: s.lineSoft,
      borderRadius: 6,
      borderRadiusLG: 8,
      borderRadiusSM: 6,
      fontFamily,
      fontSize: 13,
      controlHeight: 34,
      wireframe: false,
    },
    components: {
      Table: {
        headerBg: s.surfaceMuted,
        headerColor: s.inkMuted,
        headerSplitColor: 'transparent',
        rowHoverBg: s.surfaceHover,
        cellPaddingBlock: 17,
        cellPaddingInline: 18,
        fontSize: 12,
      },
      Tabs: { itemSelectedColor: s.primary, inkBarColor: s.primary, titleFontSize: 13 },
      Button: { fontWeight: 500, primaryShadow: 'none', defaultShadow: 'none' },
      Card: { paddingLG: 16 },
      Drawer: { paddingLG: 16 },
      Descriptions: { labelBg: s.surfaceMuted },
    },
  };
}

export const workbenchTheme = getAntdTheme('light');
