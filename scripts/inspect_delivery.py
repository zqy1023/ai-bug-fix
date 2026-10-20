import json

base = r"d:/DXM/ai_bug_fixer/runtime/runs/5a6e2d647c2e47f09602502c62f91f0b"
data = json.load(open(base + r"/steps.json", encoding="utf-8"))
for entry in data.get("tasks") or []:
    task_id = entry.get("task_id")
    recs = entry.get("prepare_delivery")
    if not recs:
        continue
    for rec in (recs if isinstance(recs, list) else [recs]):
        if isinstance(rec, dict) and rec.get("status") == "failed" and "AttributeError" in str((rec.get("error") or {}).get("message")):
            # 找对应任务终态
            fin = base + f"/tasks/{task_id}/reports/final.json"
            try:
                report = json.load(open(fin, encoding="utf-8"))
                print("task:", task_id)
                print("  report.result:", report.get("result"), "| status:", report.get("status"), "| terminal_step:", report.get("terminal_step"))
                print("  terminal_reason:", str(report.get("terminal_reason"))[:160])
                print("  next_action:", str(report.get("next_action"))[:120])
                print("  input.source_repo_path:", (rec.get("input") or {}).get("source_repo_path"))
                print("  input.analysis_only:", (rec.get("input") or {}).get("analysis_only"))
                print("  input.report_paths:", (rec.get("input") or {}).get("report_paths"))
                print("  input.ai_workspace_path:", (rec.get("input") or {}).get("ai_workspace_path"))
            except FileNotFoundError:
                print("task:", task_id, "(no final.json)")
            print()
