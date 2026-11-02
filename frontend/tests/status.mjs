import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const source = fs.readFileSync(new URL('../src/lib/status.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS}});
const context = {exports: {}};
vm.runInNewContext(compiled.outputText, context);
const {taskStatus, runStatus, needsAttention} = context.exports;
for (const prior of ['FAILED', 'PENDING', 'CHANGED_DELIVERED', 'UNCHANGED_DELIVERED', 'PAUSED']) {
  assert.equal(taskStatus(prior, 'retrying').label, '执行中');
  assert.equal(taskStatus(prior, 'cancel_requested').label, '正在停止');
  assert.equal(taskStatus(prior, 'cancelled').label, '已停止');
  assert.equal(needsAttention({result: prior, lifecycle: {status: 'retrying'}}), false);
}
assert.equal(runStatus('cancel_requested').label, '正在停止');
assert.equal(runStatus('cancelled').label, '已停止');
assert.equal(taskStatus('CHANGED_DELIVERED', 'succeeded').label, '已改动交付');
assert.equal(needsAttention({result: 'FAILED', lifecycle: {status: 'failed'}}), true);
process.stdout.write('24 lifecycle/result assertions passed\n');
