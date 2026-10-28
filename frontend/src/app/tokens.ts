/** 工作台设计令牌:primitive → semantic 两层,明暗两套取值,供 Ant Design 与原生 CSS 共用。 */

export type ThemeMode = 'light' | 'dark';

/** primitive:品牌与状态原色,不直接用于组件样式。 */
export const palette = {
  green: '#267266', greenHover: '#1E5D53', greenSoft: '#E7F1ED',
  ink: '#1B1B18', inkHover: '#33322E',
} as const;

interface SemanticSet {
  canvas: string; surface: string; surfaceMuted: string; surfaceHover: string;
  ink: string; inkMuted: string; inkFaint: string;
  line: string; lineSoft: string;
  accent: string; accentHover: string; accentContrast: string;
  primary: string; primaryHover: string; primarySoft: string; primaryContrast: string;
  success: string; successSoft: string;
  warning: string; warningSoft: string;
  danger: string; dangerSoft: string;
  shadowCard: string;
  controlFill: string; controlLine: string; tableHead: string; tableFoot: string;
  rowSelected: string; rowExpanded: string; attentionFill: string; attentionLine: string; attentionInk: string; progressTrack: string;
  nav: string; navInk: string; navMuted: string; navActive: string; navLine: string;
}

export const semantic: Record<ThemeMode, SemanticSet> = {
  light: {
    canvas: '#F6F7F9', surface: '#FFFFFF', surfaceMuted: '#F1F4F9', surfaceHover: '#F7F9FD',
    ink: '#20252E', inkMuted: '#687382', inkFaint: '#788596',
    line: '#E5E9EF', lineSoft: '#E5E9EF',
    accent: '#2253D4', accentHover: '#1844B5', accentContrast: '#FFFFFF',
    primary: '#2253D4', primaryHover: '#1844B5', primarySoft: '#EDF2FF', primaryContrast: '#FFFFFF',
    success: '#198263', successSoft: '#E9F6F0',
    warning: '#B17A20', warningSoft: '#FFF6E5',
    danger: '#C45050', dangerSoft: '#FFF0F0',
    nav: '#171B22', navInk: '#F4F6FA', navMuted: '#9CA8BB', navActive: '#283346', navLine: '#2D3543',
    controlFill: '#EAEEF4', controlLine: '#D8DFE8', tableHead: '#F8F9FB', tableFoot: '#FAFBFD',
    rowSelected: '#F7F9FE', rowExpanded: '#F6F9FD', attentionFill: '#FFF8F7', attentionLine: '#F6D0D3', attentionInk: '#87232E', progressTrack: '#D5DCE5',
    shadowCard: 'none',
  },
  dark: {
    canvas: '#11151C', surface: '#191F29', surfaceMuted: '#222B39', surfaceHover: '#242E40',
    ink: '#E8EDF5', inkMuted: '#A4B0C2', inkFaint: '#8998AF',
    line: '#303B4C', lineSoft: '#25303F',
    accent: '#7EA1FF', accentHover: '#A3BDFF', accentContrast: '#111A2C',
    primary: '#7EA1FF', primaryHover: '#A3BDFF', primarySoft: '#243556', primaryContrast: '#111A2C',
    success: '#4CC38A', successSoft: '#1A2E23',
    warning: '#D9A94E', warningSoft: '#2E2817',
    danger: '#E5766B', dangerSoft: '#33201D',
    nav: '#10151E', navInk: '#F4F6FA', navMuted: '#A2AFC3', navActive: '#28364F', navLine: '#2B3647',
    controlFill: '#222B39', controlLine: '#303B4C', tableHead: '#1C2430', tableFoot: '#1B222E',
    rowSelected: '#202C42', rowExpanded: '#1C2A40', attentionFill: '#33201D', attentionLine: '#653F3B', attentionInk: '#F0A39B', progressTrack: '#303B4C',
    shadowCard: 'none',
  },
};

export const fontFamily = '"IBM Plex Sans", "Noto Sans SC", "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif';
export const monoFamily = '"IBM Plex Mono", "JetBrains Mono", Consolas, "Noto Sans SC", "Microsoft YaHei", monospace';

const camelToKebab = (key: string) => key.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`);

/** 把当前模式的语义令牌写到 <html> 的 --wb-* 变量上;切换主题时由 ThemeProvider 调用。 */
export function applyDesignTokens(mode: ThemeMode) {
  const root = document.documentElement;
  const values = semantic[mode];
  for (const [key, value] of Object.entries(values)) {
    root.style.setProperty(`--wb-${camelToKebab(key)}`, value);
  }
  root.style.setProperty('--wb-font', fontFamily);
  root.style.setProperty('--wb-mono', monoFamily);
  root.style.setProperty('--wb-radius', '8px');
  root.style.setProperty('--wb-radius-sm', '6px');
  root.style.setProperty('color-scheme', mode);
  root.dataset.theme = mode;
}



export type { SemanticSet };
