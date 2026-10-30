import assert from 'node:assert/strict';
import { filePatch } from '../src/lib/diff.ts';

function verify(before, after) {
  const patch = filePatch({ path: 'test.py', available: true, before, after });
  const oldLines = before.match(/[^\n]*\n|[^\n]+$/g) ?? [];
  const newLines = after.match(/[^\n]*\n|[^\n]+$/g) ?? [];
  const rows = patch.split('\n');
  let oldLine = 0, newLine = 0, headers = 0;
  for (let index = 2; index < rows.length; index++) {
    const row = rows[index];
    const hunk = /^@@ -(\d+),(\d+) \+(\d+),(\d+) @@/.exec(row);
    if (hunk) {
      headers++;
      oldLine = Number(hunk[1]) - (Number(hunk[2]) ? 1 : 0);
      newLine = Number(hunk[3]) - (Number(hunk[4]) ? 1 : 0);
    } else if (row.startsWith(' ') || row.startsWith('-') || row.startsWith('+')) {
      const suffix = rows[index + 1] === '\\ No newline at end of file' ? '' : '\n';
      if (!row.startsWith('+')) assert.equal(row.slice(1) + suffix, oldLines[oldLine++]);
      if (!row.startsWith('-')) assert.equal(row.slice(1) + suffix, newLines[newLine++]);
    }
  }
  if (before !== after) assert.ok(headers > 0);
  return patch;
}

verify('', 'new\n');
verify('gone\n', '');
verify('a\r\n\r\nz', 'a\r\nb\r\nz');
verify('a\nb\nc', 'a\nx\nb\nc\n');
verify('a\na\nb\n', 'a\nb\na\n');
const before = Array.from({ length: 100 }, (_, index) => `${index}\n`).join('');
const patch = verify(before, before.replace('10\n', 'changed10\n').replace('90\n', 'changed90\n'));
assert.equal(patch.match(/^@@/gm).length, 2);
assert.ok(!patch.includes('\n 50\n'));
verify('x\n'.repeat(1500), 'y\n'.repeat(1500));
assert.equal(filePatch({ before: 'same', after: 'same' }), '');
console.log('PASS: unified hunk line numbers, separate regions, CRLF, no final newline, large rewrites');
