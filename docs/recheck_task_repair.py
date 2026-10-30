"""用真实修复 Agent 在独立副本消化归档审查发现；只产候选，不发布。"""
import argparse
import json
from pathlib import Path
import shutil
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agents.unified_fixer.graph import run_unified_fixer
from agents.unified_fixer.models import UnifiedFixerConfig
from core.contracts import TaskSnapshot
from infrastructure.llm.config import load_config
from infrastructure.workspace.diff import complete_changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task', type=Path)
    parser.add_argument('--review', type=Path)
    parser.add_argument('--execution-evidence', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--source-project', type=Path)
    parser.add_argument('--from-baseline', action='store_true', help='从只读基线创建候选，验证当前提示词的首次修复行为')
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    state = TaskSnapshot.model_validate(json.loads((args.task / 'workflow/state.json').read_text(encoding='utf-8'))['state']).to_graph()
    config = load_config()
    if args.source_project:
        state['source_project_path'] = str(args.source_project.resolve())
    task = state['task']
    feedback = {}
    if args.review:
        feedback['independent_review'] = json.loads(args.review.read_text(encoding='utf-8'))
    if args.execution_evidence:
        feedback['execution'] = json.loads(args.execution_evidence.read_text(encoding='utf-8'))
    workspace, baseline = args.output / 'workspace', args.output / 'source_snapshot'
    shutil.copytree(state['baseline_dir'] if args.from_baseline else state['ai_workspace_path'], workspace)
    shutil.copytree(state['baseline_dir'], baseline)
    state.update(carrier=task['carrier'], biztype=task['biztype'], service_name=task['service_name'],
                 run_id='independent-repair-recheck', task_id=args.task.name,
                 workspace_path=str(workspace), ai_workspace_path=str(workspace), baseline_dir=str(baseline),
                 source_snapshot={'baseline_dir': str(baseline), 'baseline_manifest': state['baseline_manifest']},
                 retry_reason=json.dumps(feedback, ensure_ascii=False) if feedback else '')

    def invoke(request, call):
        (args.output / 'fixer-request.json').write_text(request.model_dump_json(indent=2), encoding='utf-8')
        raw = call()
        (args.output / 'fixer-raw.json').write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding='utf-8')
        return raw

    with patch('agents.unified_fixer.provider.invoke_with_audit', side_effect=invoke):
        result = run_unified_fixer(state, UnifiedFixerConfig(model=config.claude.model))
    state.update(unified_fixer=result['unified_fixer'], changed_files=result['changed_files'])
    (args.output / 'workflow').mkdir()
    (args.output / 'artifacts').mkdir()
    (args.output / 'workflow/state.json').write_text(json.dumps({'state': TaskSnapshot.from_graph(state).model_dump(mode='json')}, ensure_ascii=False, indent=2), encoding='utf-8')
    (args.output / 'artifacts/diff.json').write_text(json.dumps(complete_changes(workspace, baseline), ensure_ascii=False, indent=2), encoding='utf-8')
    validation_path = args.task / 'artifacts/validation.json'
    if validation_path.is_file():
        shutil.copyfile(validation_path, args.output / 'artifacts/validation.json')
    (args.output / 'fixer-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: result.get(key) for key in ('status', 'summary', 'changed_files', 'failure_kind')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
