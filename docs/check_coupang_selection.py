"""执行真实源码中的字段提取语句，记录最小输入的旧新行为；不调用网络或替代业务回放。"""
import argparse
import ast
import hashlib
import json
from pathlib import Path

from lxml import etree


def extraction(path):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    carrier = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Coupang')
    method = next(node for node in carrier.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'process_html')
    loop = next(node for node in ast.walk(method) if isinstance(node, ast.For) and isinstance(node.target, ast.Name)
                and node.target.id == 'event')
    statements = []
    for node in loop.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'event' for target in node.targets):
            break
        statements.append(node)
    return compile(ast.Module(body=statements, type_ignores=[]), str(path), 'exec')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--original-patch', type=Path, required=True)
    parser.add_argument('--minimal-patch', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    inputs = {'empty': '<td></td>', 'single': '<td>A</td>',
              'mixed_text': '<td>A<span>B</span>C</td>',
              'whitespace_and_nested': '<td> <span>B</span> C</td>'}
    programs = {name: extraction(path) for name, path in (
        ('baseline', args.baseline), ('original_patch', args.original_patch), ('minimal_patch', args.minimal_patch))}
    rows = []
    for name, cell in inputs.items():
        row = {'sample': name, 'html': '<table><tr><td>2026-10-30</td>' + cell + '<td>Status</td></tr></table>', 'results': {}}
        for version, program in programs.items():
            context = {'event': etree.HTML(row['html']).xpath('//tr')[0]}
            try:
                exec(program, context)
                row['results'][version] = {key: context[key] for key in ('location', 'description')}
            except Exception as error:
                row['results'][version] = {'exception': type(error).__name__}
        rows.append(row)
    report = {'kind': 'source_statement_differential', 'scope': '只执行真实源码中事件构造之前的字段提取语句；不代表完整框架或真实请求验证',
              'sources': {name: {'path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()} for name, path in (
                  ('baseline', args.baseline), ('original_patch', args.original_patch), ('minimal_patch', args.minimal_patch))},
              'cases': rows}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(rows, ensure_ascii=False))


if __name__ == '__main__':
    main()
