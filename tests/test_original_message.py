import importlib
import unittest
import json

from infrastructure.es.logs import ESLogAdapter
from infrastructure.es.original_message import original_message

normalize = importlib.import_module('workflow.stages.log_pipeline.normalize_error.node')._normalize
consume = importlib.import_module('workflow.stages.log_pipeline.deduplicate_error.node').consume


class OriginalMessageTests(unittest.TestCase):
    def logs(self):
        response = '{"errcode":999999,"errmsg":"system error,try again later","data":null}'
        payload = {"track_config": {"proxy_channel_model": {"biz_ident": "packet_ESPoirer", "provider": "proxy_scrape", "proxy_type": "datacenter"}, "timeout": 30000},
                   "carrier": "ESPoirer", "response": response, "requests": [{"number": "461231269991", "gather_channel": "Default"}]}
        prefix = '2026-10-29 15:35:07,054 [172.17.209.53-10] ERROR gather.py.track - '
        model = prefix + 'framework.track_server:解析异常,model:' + json.dumps(payload, ensure_ascii=False, separators=(',', ':')) + ', msg:None'
        inline = prefix + 'common.base_func:Carrier:ESPoirer,number:461231269991,msg:' + response
        return model, inline

    def records(self, messages):
        values = []
        for message in messages:
            biztype = 'common.base_func' if 'common.base_func:' in message else 'framework.track_server'
            parsed = ESLogAdapter.parse_hit({'_source': {'service_name': 'gather.py.track', 'biztype': biztype, 'message': message}})
            values.append({'problem': parsed})
        return values

    def test_model_original_is_restored_without_discarding_trigger_log(self):
        model, inline = self.logs()
        records = self.records([inline, model])
        result = original_message(records[0]['problem'], records)
        self.assertEqual(result['message'], model)
        self.assertEqual(result['trigger_message'], inline)
        self.assertEqual(result['related_messages'], [inline])
        self.assertFalse(result['truncated'])
        self.assertEqual(result['source_biztype'], 'framework.track_server')

    def test_different_request_time_or_thread_cannot_replace_original(self):
        model, inline = self.logs()
        for changed in (model.replace('461231269991', 'ANOTHER'), model.replace('15:35:07,054', '15:35:08,054'), model.replace('209.53-10', '209.53-11')):
            records = self.records([inline, changed])
            self.assertEqual(original_message(records[0]['problem'], records)['message'], inline)

    def test_long_original_is_recovered_exactly_from_archive(self):
        message = '  full original\n' + 'frame\n' * 2000 + '\n  END  '
        problem = {'carrier': 'TEST', 'biztype': 'track', 'service_name': 'test', 'error': 'boom', 'req': '{}', 'message': message}
        clipped = {**problem, 'message': message[:600] + f'…(已截断，原长 {len(message)} 字符)'}
        result = original_message(clipped, [{'problem': problem}])
        self.assertEqual(result['message'], message)
        self.assertFalse(result['truncated'])

    def test_agent_handoff_keeps_trigger_and_original_exception(self):
        from workflow.stages.log_pipeline.identify_carrier.node import task_problem
        model, inline = self.logs()
        records = self.records([inline, model])
        problem = task_problem({'problem': records[0]['problem']}, records)
        self.assertEqual(problem['message'], inline)
        self.assertEqual(problem['original_exception_message'], model)

    def test_es_to_deduplicated_problem_preserves_full_message(self):
        message = ('  request context\n' + 'stack frame\n' * 120
                   + 'req:{"carrier":"TEST","number":"001"} error: ValueError: invalid value\n  ')
        record = ESLogAdapter.parse_hit({'_source': {
            'service_name': 'test-service', 'biztype': 'track', 'message': message,
        }})
        self.assertIsNotNone(record)
        self.assertEqual(record['message'], message)
        groups = {}
        consume(groups, normalize(record, 1), ())
        problem = next(iter(groups.values()))['problem']
        self.assertEqual(problem['message'], message)
        self.assertNotEqual(problem['error'], message)


if __name__ == '__main__':
    unittest.main()
