import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from infrastructure.llm.common import _audit_events, invoke_with_audit
from infrastructure.llm.session import _public_events


class AgentEventCaptureTests(unittest.TestCase):
    def test_thinking_and_tool_stream_are_retained(self):
        message = SimpleNamespace(content=[
            SimpleNamespace(thinking='先核对 destinationEta 的空值分支'),
            SimpleNamespace(name='Read', input={'file_path': 'main.py'}, id='tool-1'),
            SimpleNamespace(tool_use_id='tool-1', is_error=False, content='86  eta = routing.get(...)'),
            SimpleNamespace(text='结论：代码修复成立，业务未验证'),
        ])

        events = _public_events(message)
        self.assertEqual(events[0], {'type': 'thinking', 'text': '先核对 destinationEta 的空值分支'})
        self.assertEqual([item['type'] for item in events], ['thinking', 'tool_use', 'tool_result', 'assistant'])

        audit = _audit_events(events)
        self.assertEqual([item['type'] for item in audit], ['thinking', 'tool_use', 'tool_result', 'assistant'])
        self.assertEqual(audit[1]['tool'], 'Read')
        self.assertIn('main.py', audit[1]['input']['file_path'])



class AgentAuditRetentionTests(unittest.TestCase):
    def test_compact_run_keeps_public_output_and_prompt_for_success_and_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            run_root = Path(directory) / 'runs/run'
            run_root.mkdir(parents=True)
            (run_root / 'run.json').write_text('{"artifact_mode":"compact"}')
            request = SimpleNamespace(
                run_id='run', task_id='task', agent_role='unified_fixer', attempt=1,
                model='test', provider='test', prompt_version='1', prompt_hash='hash',
                logical_context_hash='', previous_context_hash='', context_mode='full',
                system_prompt='只读分析源码', prompt='检查真实异常', prompt_context={},
                workspace=directory, allowed_read_roots=(), allowed_write_roots=(),
            )
            records = []
            config = SimpleNamespace(runtime_dir=directory, workbench_prompt_snapshot=True)

            def fail():
                error = RuntimeError('调用失败')
                error.agent_events = [{'type': 'assistant', 'text': '已读取源码，调用中断'}]
                raise error

            with patch('infrastructure.llm.config.load_config', return_value=config), \
                 patch('infrastructure.llm.common._persist_model_call', side_effect=records.append):
                invoke_with_audit(request, lambda: {'result': '公开分析结论'})
                with self.assertRaises(RuntimeError):
                    invoke_with_audit(request, fail)
            self.assertEqual(len(records), 2)
            for record in records:
                self.assertEqual(record['event_persistence_status'], 'persisted')
                self.assertGreater(record['event_count'], 0)
                snapshot = json.loads((run_root / record['prompt_snapshot']).read_text(encoding='utf-8'))
                self.assertEqual(snapshot['task_prompt'], request.prompt)
                events = [json.loads(line) for line in (run_root / record['events_path']).read_text(encoding='utf-8').splitlines()]
                self.assertTrue(any(event['model_call_id'] == record['model_call_id'] for event in events))


if __name__ == '__main__':
    unittest.main()
