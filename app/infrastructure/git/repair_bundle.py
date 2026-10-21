"""在交付检出中生成可复核方案和离线验证脚本，不污染 AI 源码工作区。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from app.infrastructure.filesystem.workspace import WorkspaceManager
from app.infrastructure.redaction import redact_output

_VERIFY_SCRIPT = '''"""验证修复源码；固定业务回归需显式 --regression。"""
import argparse, ast, json, pathlib, subprocess, sys
parser = argparse.ArgumentParser()
parser.add_argument('--repo', required=True, help='业务项目根目录')
parser.add_argument('--regression', action='store_true')
args = parser.parse_args()
root = pathlib.Path(args.repo).resolve()
bundle = json.loads(pathlib.Path(__file__).with_name('repair_plan.json').read_text(encoding='utf-8'))
for relative in bundle.get('changed_files', []):
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise SystemExit('文件路径越界')
    if target.suffix == '.py' and target.is_file():
        ast.parse(target.read_text(encoding='utf-8'), filename=relative)
        print('SYNTAX PASS', relative)
print('语法检查完成；业务验证结果以 validation 中的实际记录为准。')
if args.regression:
    commands = bundle.get('validation', {}).get('run_regression', {}).get('commands', [])
    if not commands:
        raise SystemExit('没有记录可重跑的固定回归命令，不能声称业务验证通过')
    for item in commands:
        command = list(item['command'])
        if pathlib.Path(command[0]).name.lower() in ('python', 'python.exe', 'python3', 'python3.exe'):
            command[0] = sys.executable
        print('RUN', command, flush=True)
        subprocess.run(command, cwd=root, check=True)
'''


def write_repair_bundle(project: Path, state: Mapping[str, Any]) -> list[str]:
    """只写入当前 carrier 的交付附件，方案及命令均来自持久化步骤。"""
    bundle = state.get("repair_bundle")
    if not isinstance(bundle, dict) or not bundle:
        return []
    roots = WorkspaceManager.locate_carrier_roots(project, str(state.get("carrier") or ""))
    if not roots:
        raise ValueError("交付中不存在目标运输商目录")
    task_id = WorkspaceManager._safe_name(str(state.get("task_id") or "repair"))
    folder = project / roots[0] / "repair_records" / task_id
    if not folder.resolve().is_relative_to((project / roots[0]).resolve()):
        raise ValueError("修复附件路径越界")
    folder.mkdir(parents=True, exist_ok=True)
    body = {**bundle, "changed_files": list(state.get("changed_files") or [])}
    files = {
        "repair_plan.json": redact_output(json.dumps(body, ensure_ascii=False, indent=2)),
        "repair.patch": str(bundle.get("diff") or ""),
        "verify_repair.py": _VERIFY_SCRIPT,
        "README.md": "# 修复方案与验证\n\n根因、证据、变更方案及实际验证输出见 repair_plan.json。\n\n"
        "```sh\npython verify_repair.py --repo <业务项目根目录>\n"
        "python verify_repair.py --repo <业务项目根目录> --regression\n```\n\n"
        "repair.patch 用于核对修复差异。语法通过不等同于业务重放通过；未验证事项保留在报告内。\n",
    }
    for name, content in files.items():
        (folder / name).write_text(content, encoding="utf-8")
    return [(folder / name).relative_to(project).as_posix() for name in files]
