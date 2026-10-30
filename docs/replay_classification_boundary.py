"""离线构造相邻输入，执行归档 ShipmentLink 补丁前后真实解析函数；不请求官网。"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'infrastructure/workspace'))
from runtime_environment import environment_receipt, prepare_runtime_logging


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task', type=Path)
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    os.environ.pop('SSLKEYLOGFILE', None)
    prepare_runtime_logging()
    sys.path.insert(0, str(args.project.resolve()))
    from yq_common.log_utils import LogUtils
    LogUtils.initialize('gather.py.track')
    from framework.models import TrackModel

    diff = json.loads((args.task / 'artifacts/diff.json').read_text(encoding='utf-8'))['files'][0]
    classes = {}
    for phase in ('before', 'after'):
        namespace = {'__name__': 'audit_' + phase}
        exec(compile(diff[phase], diff['path'], 'exec'), namespace)
        classes[phase] = namespace['ShipmentLink']
    inputs = {}
    observations = []
    for deferred in (False, True):
        html = '<html><body><table><tr><th>Place of Receipt</th><td>Shanghai</td></tr></table>'
        if deferred:
            html += "<script>function validateOtherInput(){alert('B/L No. is not valid');}</script>"
        html += '</body></html>'
        response = {'bl': html, 'moves': [{'html': '<table><tr><td>Oct-29-2026</td><td>Discharged</td><td>Shanghai</td><td>TEST</td></tr></table>'}]}
        inputs[str(deferred)] = response
    inputs['empty_deferred'] = {'bl': "<html><body><script>function validateOtherInput(){alert('B/L No. is not valid');}</script></body></html>", 'moves': []}
    inputs['inactive_condition'] = {'bl': "<script>if(false){alert('B/L No. is not valid');}</script>", 'moves': []}
    inputs['comment_only'] = {'bl': "<script>/* B/L No. is not valid */</script>", 'moves': []}
    inputs['inactive_unbraced'] = {'bl': "<script>if(false) alert('B/L No. is not valid'); if(false) window.location.href='/tdb1/jsp/ct/TDB1_Tracking.jsp';</script>", 'moves': []}
    inputs['inert_script'] = {'bl': "<script type='application/json'>alert('B/L No. is not valid'); window.location.href='/tdb1/jsp/ct/TDB1_Tracking.jsp';</script>", 'moves': []}
    inputs['external_script_inline_text'] = {'bl': "<script src='/tracking.js'>alert('B/L No. is not valid'); window.location.href='/tdb1/jsp/ct/TDB1_Tracking.jsp';</script>", 'moves': []}
    inputs['template_script'] = {'bl': "<template><script>alert('B/L No. is not valid'); window.location.href='/tdb1/jsp/ct/TDB1_Tracking.jsp';</script></template>", 'moves': []}
    state = json.loads((args.task / 'workflow/state.json').read_text(encoding='utf-8'))['state']
    recorded = (state.get('identity') or {}).get('problem', {}).get('response')
    if recorded:
        inputs['recorded_original'] = json.loads(recorded)
    for case, response in inputs.items():
        for phase, cls in classes.items():
            model = TrackModel.model_validate({'carrier': 'ShipmentLink', 'requests': [{'number': 'AUDIT-SYNTHETIC'}],
                                              'response': json.dumps(response)})
            row = {'phase': phase, 'case': case}
            try:
                result = asyncio.run(cls().process_html(model))
                item = result.items[0]
                track = item.track_result
                row.update(error_type=str(item.error_type), sub_status=str(track.sub_status),
                           events=len(track.track_result_events or []))
            except Exception as error:
                row.update(exception_class=type(error).__name__, message=str(error))
            observations.append(row)
    report = {'source': '构造的离线相邻输入，非官网正常响应，不证明线上发生频率',
              'environment': environment_receipt(), 'inputs': inputs, 'observations': observations}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(observations, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
