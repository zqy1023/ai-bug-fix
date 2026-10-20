import type { ThemeConfig } from 'antd';

/** 浅色企业蓝：与 styles.css 中的 CSS 变量保持同源。 */
export const palette = {
  primary: '#1B4DD8',
  primaryHover: '#2E63F0',
  primarySoft: '#E8F0FF',
  canvas: '#F5F7FA',
  surface: '#FFFFFF',
  surfaceMuted: '#EFF2F6',
  ink: '#0F172A',
  inkMuted: '#475569',
  inkFaint: '#94A3B8',
  success: '#16A34A',
  danger: '#DC2626',
  warning: '#D97706',
  neutral: '#6B7280',
} as const;

/** 结果分类色调：Task 是最高优先级观察对象，颜色只做辅助编码。 */
export type Tone = 'running' | 'waiting' | 'success' | 'danger' | 'manual' | 'neutral' | 'pending';

export const toneColor: Record<Tone, string> = {
  running: palette.primary,
  waiting: palette.warning,
  success: palette.success,
  danger: palette.danger,
  manual: palette.neutral,
  neutral: palette.inkFaint,
  pending: palette.inkFaint,
};

export const workbenchTheme: ThemeConfig = {
  token: {
    colorPrimary: palette.primary,
    colorInfo: palette.primary,
    colorSuccess: palette.success,
    colorWarning: palette.warning,
    colorError: palette.danger,
    colorTextBase: palette.ink,
    colorText: palette.ink,
    colorTextSecondary: palette.inkMuted,
    colorTextTertiary: palette.inkFaint,
    colorBgLayout: palette.canvas,
    colorBgContainer: palette.surface,
    colorBorder: '#E2E8F0',
    colorBorderSecondary: '#EDF1F6',
    borderRadius: 8,
    borderRadiusLG: 10,
    borderRadiusSM: 6,
    fontFamily: '"PingFang SC", "Microsoft YaHei", Inter, system-ui, sans-serif',
    fontSize: 13,
    controlHeight: 34,
    wireframe: false,
  },
  components: {
    Table: {
      headerBg: '#F8FAFC',
      headerColor: palette.inkMuted,
      headerSplitColor: 'transparent',
      rowHoverBg: '#F5F8FF',
      cellPaddingBlock: 10,
      cellPaddingInline: 12,
      fontSize: 12,
    },
    Tabs: { itemSelectedColor: palette.primary, inkBarColor: palette.primary, titleFontSize: 13 },
    Button: { fontWeight: 500, primaryShadow: 'none', defaultShadow: 'none' },
    Card: { paddingLG: 16 },
    Drawer: { paddingLG: 16 },
    Descriptions: { labelBg: '#F8FAFC' },
  },
};
