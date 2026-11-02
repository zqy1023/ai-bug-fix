"""临时 ES 客户端：走 Kibana Console Proxy，替代直连 ES。

**用途**：项目正式配置 ``LOG_ES`` 指向腾讯云 ES（直连 9200），当前出口 IP 不在
其白名单内，直连会被 nginx 网关以 403 拦掉。内网 Kibana ``https://kq.17track.net``
的 Console Proxy 可用（``_search`` 已实测 200），因此临时用它把整条流程跑通。

**这不是项目代码**：本文件放在 ``docs/`` 下，仅供一次性排查使用，不进主流程、
不被 ``infrastructure`` 引用。腾讯云 ES 恢复后应直接删掉本文件并把 ``LOG_ES``
改回直连地址。

用法（临时覆盖 ES 客户端后跑一次完整流程）::

    python docs/kibana_es_client.py          # 冒烟：拉一条日志看看
    python server.py                         # 正常按项目配置跑（直连仍失败）

Kibana Console Proxy 的用法::

    POST https://<kibana>/api/console/proxy?path=<es_path>&method=POST
    必需请求头 kbn-xsrf: <任意非空值>
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class KibanaConsoleProxyClient:
    """按 ``elasticsearch7`` 的 client 接口形状封装 Console Proxy。

    ``ESLogAdapter`` 只用 ``client.search`` / ``client.scroll``（经
    ``elasticsearch7.helpers.scan``），这里实现这两个方法并返回 ES 原始 JSON。
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        timeout: int = 30,
        verify: bool = False,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._auth = base64.b64encode(f"{username}:{password}".encode()).decode()
        self._timeout = timeout
        self._verify = verify

    # ------------------------------------------------------------------ HTTP

    def _request(
        self,
        es_path: str,
        method: str,
        body: dict[str, Any] | None,
        extra_query: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        params = {"path": es_path, "method": method}
        params.update(extra_query or {})
        query = urllib.parse.urlencode(params)
        url = f"{self._base}/api/console/proxy?{query}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "Authorization": f"Basic {self._auth}",
            "kbn-xsrf": "ai-bug-fixer",
            "Accept": "application/json",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, headers=headers, data=data, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")
            raise RuntimeError(
                f"Console Proxy 请求失败 {error.code} path={es_path}: {detail[:400]}"
            ) from error
        except Exception as error:  # noqa: BLE001 - 排查脚本，保留原始原因
            raise RuntimeError(f"Console Proxy 请求异常 path={es_path}: {error}") from error
        try:
            payload = json.loads(raw)
        except ValueError as error:
            raise RuntimeError(f"Console Proxy 返回非 JSON path={es_path}: {raw[:300]}") from error
        if isinstance(payload, dict) and payload.get("error"):
            raise RuntimeError(f"Console Proxy 返回 ES 错误 path={es_path}: {json.dumps(payload)[:400]}")
        return payload if isinstance(payload, dict) else {"hits": {"hits": []}}

    # ------------------------------------------------------- ES client 接口

    def search(self, index: str = "", body: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        """发起一次 ``_search``；``body`` 与 ``query`` 两种入参都接受。

        ``elasticsearch7.helpers.scan`` 会把 body 里的查询字典**摊平**传进来
        （``search_kwargs.update(query)``），所以这里必须区分两种形状：
        传进来的已经是完整 body（含 ``query`` 键）就直接用，否则按 ES 语法包一层。

        另一个坑：scan 把 ``scroll`` 也摊平成字符串塞进 body，但 ES 的 ``scroll``
        是 **URL 查询参数**、不是 body 字段，直接转发会报
        ``Unknown key for a VALUE_STRING in [scroll]``。这里把它从 body 摘出来
        拼进 Console Proxy 的查询串。
        """
        payload: dict[str, Any] = dict(body or {})
        query = kwargs.pop("query", None)
        if query:
            if isinstance(query, dict) and "query" in query:
                payload.update(query)
            else:
                payload["query"] = query
        size = kwargs.pop("size", None)
        if size is not None:
            payload["size"] = size
        scroll = kwargs.pop("scroll", None)
        if isinstance(scroll, str):
            payload.pop("scroll", None)
        elif scroll:
            payload["scroll"] = scroll
        path = f"{index}/_search" if index else "_search"
        # Console Proxy 会把 path 当成完整 ES 路径解析，scroll 必须是路径里的查询串
        # （报 "[request query.scroll]: definition for this key is missing"）。
        if isinstance(scroll, str):
            path = f"{path}?scroll={urllib.parse.quote(scroll, safe='')}"
        return self._request(path, "POST", payload)

    def scroll(self, scroll_id: str, scroll: str = "2m", **kwargs: Any) -> dict[str, Any]:
        """继续滚动已建立的 scroll 上下文。"""
        path = "_search/scroll"
        return self._request(path, "POST", {"scroll": scroll, "scroll_id": scroll_id})

    def clear_scroll(self, scroll_id: str, **kwargs: Any) -> dict[str, Any]:
        """释放 scroll 上下文（Console Proxy 下尽力而为，失败不阻断）。"""
        try:
            return self._request("_search/scroll", "DELETE", None)
        except Exception:  # noqa: BLE001 - 清理失败不应影响主流程
            return {"succeeded": False}


def client_from_env(timeout: int = 30) -> KibanaConsoleProxyClient:
    """从环境变量构造临时客户端。"""
    import os

    base = os.getenv("AI_REPAIR_KIBANA_URL", "https://kq.17track.net")
    user = os.getenv("AI_REPAIR_KIBANA_USER", "Kervin")
    password = os.getenv("AI_REPAIR_KIBANA_PASSWORD", "")
    if not password:
        raise SystemExit("缺少 AI_REPAIR_KIBANA_PASSWORD 环境变量")
    return KibanaConsoleProxyClient(base, user, password, timeout=timeout)


if __name__ == "__main__":
    from elasticsearch7.helpers import scan

    proxy = client_from_env()
    hits = scan(
        client=proxy,
        query={"query": {"match_all": {}}},
        index="logstash-gather-*",
        size=2,
        scroll="1m",
        raise_on_error=True,
        request_timeout=30,
    )
    for hit in hits:
        source = hit.get("_source") or {}
        print("index:", hit.get("_index"), "id:", hit.get("_id"))
        print("service_name:", source.get("service_name"))
        print("biztype:", source.get("biztype"))
        print("message:", str(source.get("message") or "")[:400])
        break
