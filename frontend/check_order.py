from playwright.sync_api import sync_playwright
RUN="aaf185a1dc264b8fb23235bc70aa0225"; TASK="JAS__item__9283a27fb875"
with sync_playwright() as p:
    b=p.chromium.launch(headless=True)
    page=b.new_page(viewport={"width":1440,"height":1000})
    page.goto(f"http://127.0.0.1:5173/runs/{RUN}/tasks/{TASK}", wait_until="domcontentloaded")
    page.wait_for_selector(".node-path", timeout=30000)
    page.wait_for_timeout(1000)
    print("path:", page.locator(".node-path").inner_text().replace("\n"," | "))
    print("late_path_items:", page.locator(".node-path-list li.is-late button").all_inner_texts())
    b.close()
