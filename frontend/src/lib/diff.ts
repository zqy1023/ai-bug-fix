import type { FileChange } from './types';

/** 从完整前后文本计算行差异，原始内容继续用于源码视图。 */
export function filePatch(file: FileChange): string {
  if (file.patch !== undefined) return file.patch;
  const lines = (text: string) => text.match(/[^\n]*\n|[^\n]+$/g) ?? [];
  const before = lines(file.before || ''), after = lines(file.after || '');
  if (file.before === file.after) return '';
  let prefix = 0, suffix = 0;
  while (prefix < before.length && prefix < after.length && before[prefix] === after[prefix]) prefix++;
  while (suffix < before.length - prefix && suffix < after.length - prefix
    && before[before.length - suffix - 1] === after[after.length - suffix - 1]) suffix++;
  const a = before.slice(prefix, before.length - suffix), b = after.slice(prefix, after.length - suffix);
  const operations: [string, string][] = before.slice(0, prefix).map(line => [' ', line]);
  // 大规模完全重写采用整段替换，避免二次方内存；完整内容仍全部显示。
  if (a.length * b.length > 2_000_000) {
    operations.push(...a.map(line => ['-', line] as [string, string]), ...b.map(line => ['+', line] as [string, string]));
  } else {
    const width = b.length + 1, table = new Uint32Array((a.length + 1) * width);
    for (let i = a.length - 1; i >= 0; i--) for (let j = b.length - 1; j >= 0; j--) {
      table[i * width + j] = a[i] === b[j] ? table[(i + 1) * width + j + 1] + 1
        : Math.max(table[(i + 1) * width + j], table[i * width + j + 1]);
    }
    let i = 0, j = 0;
    while (i < a.length || j < b.length) {
      if (i < a.length && j < b.length && a[i] === b[j]) { operations.push([' ', a[i++]]); j++; }
      else if (i < a.length && (j === b.length || table[(i + 1) * width + j] >= table[i * width + j + 1])) operations.push(['-', a[i++]]);
      else operations.push(['+', b[j++]]);
    }
  }
  operations.push(...before.slice(before.length - suffix).map(line => [' ', line] as [string, string]));
  const groups: [number, number][] = [];
  operations.forEach(([kind], index) => {
    if (kind === ' ') return;
    const start = Math.max(0, index - 3), end = Math.min(operations.length, index + 4);
    const last = groups.at(-1);
    if (last && start <= last[1]) last[1] = end;
    else groups.push([start, end]);
  });
  const oldPositions = [1], newPositions = [1];
  operations.forEach(([kind], index) => {
    oldPositions.push(oldPositions[index] + (kind === '+' ? 0 : 1));
    newPositions.push(newPositions[index] + (kind === '-' ? 0 : 1));
  });
  return `--- a/${file.path}\n+++ b/${file.path}\n` + groups.map(([start, end]) => {
    const oldCount = oldPositions[end] - oldPositions[start], newCount = newPositions[end] - newPositions[start];
    const header = `@@ -${oldPositions[start] - (oldCount ? 0 : 1)},${oldCount} +${newPositions[start] - (newCount ? 0 : 1)},${newCount} @@\n`;
    return header + operations.slice(start, end).map(([kind, line]) =>
      kind + line + (line.endsWith('\n') ? '' : '\n\\ No newline at end of file\n')).join('');
  }).join('');
}
