import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

import { applyDesignTokens, applyLegacyAliases, type ThemeMode } from './tokens';
import { getAntdTheme } from './theme';

const STORAGE_KEY = 'wb-theme';

function initialMode(): ThemeMode {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === 'light' || saved === 'dark') return saved;
  } catch { /* localStorage 不可用时跟随系统 */ }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

// 首帧渲染前先写入令牌,避免主题闪烁;此后由 effect 接管(跟随系统变化)。
applyDesignTokens(initialMode());
applyLegacyAliases();

interface ThemeContextValue {
  mode: ThemeMode;
  toggle: () => void;
}

const ThemeContext = createContext<ThemeContextValue>({ mode: 'light', toggle: () => {} });

export function useThemeMode(): ThemeContextValue {
  return useContext(ThemeContext);
}

/**
 * 主题来源:显式选择持久化到 localStorage;未选择时跟随系统并监听变化。
 * 模式变化时同步写 --wb-* 语义变量与 <html data-theme>,AntD 由外层 ConfigProvider 消费同一模式。
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<ThemeMode>(initialMode);

  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const follow = (event: MediaQueryListEvent) => {
      if (!localStorage.getItem(STORAGE_KEY)) setMode(event.matches ? 'dark' : 'light');
    };
    media.addEventListener('change', follow);
    return () => media.removeEventListener('change', follow);
  }, []);

  useEffect(() => {
    applyDesignTokens(mode);
    applyLegacyAliases();
    document.documentElement.dataset.theme = mode;
  }, [mode]);

  const toggle = useCallback(() => {
    setMode((current) => {
      const next: ThemeMode = current === 'dark' ? 'light' : 'dark';
      try { localStorage.setItem(STORAGE_KEY, next); } catch { /* 忽略持久化失败 */ }
      return next;
    });
  }, []);

  const value = useMemo(() => ({ mode, toggle }), [mode, toggle]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export { getAntdTheme };
