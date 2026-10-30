"""使用当前真实审查模型只读复审归档补丁；不发布、不改写历史任务。"""
import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.review.contract import ReviewConfig
from agents.review.graph import ClaudeQueryProvider, review
from core.contracts import TaskSnapshot
from infrastructure.llm.config import load_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--execution-evidence', type=Path, help='补充独立执行回执，不提供预设审查结论')
    parser.add_argument('--rerun-validation', action='store_true', help='对当前候选执行真实回放与请求验证')
    args = parser.parse_args()
    config = load_config()
    args.output.mkdir(parents=True, exist_ok=True)
    archived = json.loads((args.task / 'workflow/state.json').read_text(encoding='utf-8'))
    state = TaskSnapshot.model_validate(archived['state']).to_graph()
    task = state.get('task') or {}
    for key in ('carrier', 'biztype', 'service_name'):
        state[key] = task.get(key) or (state.get('problem') or {}).get(key)
    if args.rerun_validation:
        from infrastructure.workspace.validation import run_validation
        validation = run_validation(state)
        (args.output / 'validation.json').write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding='utf-8')
    else:
        validation = json.loads((args.task / 'artifacts/validation.json').read_text(encoding='utf-8'))
    validation['recheck_provenance'] = {
        'mode': 'fresh_validation' if args.rerun_validation else 'archived_validation',
        'source_task': str(args.task.resolve()),
        'note': '归档执行结果不代表当前环境已重新验证；新增执行证据单独标明来源。',
    }
    if args.execution_evidence:
        validation['additional_execution'] = json.loads(args.execution_evidence.read_text(encoding='utf-8'))
    env = config.agent_env()
    env.pop('SSLKEYLOGFILE', None)
    provider = ClaudeQueryProvider(ReviewConfig(provider='claude_query'), env)

    def invoke(request, call):
        (args.output / 'request.json').write_text(request.model_dump_json(indent=2), encoding='utf-8')
        raw = call()
        (args.output / 'raw.json').write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding='utf-8')
        return raw

    with tempfile.TemporaryDirectory(prefix='review-recheck-') as temporary:
        workspace = Path(temporary) / 'workspace'
        shutil.copytree(state['ai_workspace_path'], workspace)
        state.update(ai_workspace_path=str(workspace), run_id='independent-review-recheck',
                     task_id=args.task.name, repair=state.get('unified_fixer') or {})
        # 复用真实历史执行证据，不伪造新业务执行、不调用承运商。
        with patch('infrastructure.workspace.validation.run_validation', return_value=validation), \
             patch('agents.review.graph.invoke_with_audit', side_effect=invoke):
            result = review(state, provider)
    (args.output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: result.get(key) for key in ('verdict', 'code_verdict', 'delivery_eligible', 'contract_error', 'summary')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
