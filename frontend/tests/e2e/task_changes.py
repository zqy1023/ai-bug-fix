"""读取当前服务的真实 CourierPlusNigeria 产物，验证差异与完整源码。"""
import json
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect


def main():
    root = Path(__file__).resolve().parents[3]
    run = "2b9b2b8abe044b07b5c77d5b5b54af86"
    task = next((root / "runtime/runs" / run / "tasks").glob("CourierPlusNigeria*" )).name
    with urlopen(f"http://127.0.0.1:8000/api/v1/runs/{run}/artifacts/download?path=artifacts/diff.json&task_id={task}", timeout=20) as response:
        original = json.load(response)["files"][0]
    output = root / "runtime/ui-check"
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1080})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"http://127.0.0.1:5173/runs/{run}/tasks/{task}/change")
        file = page.locator(".file-change").first
        expect(file).to_be_visible(timeout=60000)
        expect(file.locator("summary")).to_contain_text(original["path"])
        assert file.locator(".change-line.add").count() > 0
        assert file.locator(".change-line.del").count() > 0
        assert page.get_by_text("没有 diff.patch", exact=True).count() == 0
        page.screenshot(path=str(output / "courierplus-real-diff.png"), full_page=True)
        file.get_by_role("button", name="修改前", exact=True).click()
        before = file.locator(".change-line.source code").all_text_contents()
        assert "\n".join(before).replace("\r", "").rstrip() == original["before"].replace("\r", "").rstrip()
        file.get_by_role("button", name="修改后", exact=True).click()
        after = file.locator(".change-line.source code").all_text_contents()
        assert "\n".join(after).replace("\r", "").rstrip() == original["after"].replace("\r", "").rstrip()
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "页面整体横向溢出"
        page.screenshot(path=str(output / "courierplus-source-mobile.png"), full_page=True)
        assert not errors, errors
        browser.close()
    print("PASS: real API artifact, added/deleted lines, exact before/after source, mobile layout")


if __name__ == "__main__":
    main()
