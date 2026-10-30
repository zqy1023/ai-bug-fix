<!-- prompt-version: 1.1.0 -->
## 页面拦截规则

本规则仅在已取得明确错误正文、证明本地处理缺陷并通过“是否应修改”判断后适用。纯网络/环境故障或已被正确处理的上游拒绝直接跳过；不能为了套用本规则主动扩大任务或修改请求策略。

1. WAF、反爬、机器人验证、验证码/登录拦截、访问拒绝统一为 `ServiceError`，HTTP 200 也适用。
2. 识别已捕获响应中的明确拦截结构或标识，例如 `Unauthorized Request Blocked`、`Unauthorized Activity Detected`、
   `Access Denied`、`you are a bot`、`Verifying your browser`、`Incapsula_Resource`。
3. 在 JSON/XML/轨迹解析前识别；不能只因为有 HTML 标签就判拦截，也不能将所有 JSON 解析失败都归为拦截。
4. 不返回 NotFound、NoDataError、空轨迹或成功；不能只改异常文本而保留裸 Exception。
5. 本轮只修错误分类，不扩展为 WAF 绕过、登录重写、代理策略或重试策略变更。
6. 以上标识是识别参考，不是要求每个 carrier 复制整张关键词表。
   只针对本轮已捕获响应中的明确特征，在已有解析方法内局部判断；不得新增 `_blocked_keywords` 类属性或 `_check_blocked` helper。
7. 异常处理验证必须证明：同一原始正文到达真实解析入口、基线复现原异常及位置、修复后命中对应处理并返回明确原因与正确协议。
   ServiceError 是框架分类，不能单凭分类宣称上游失败或修复正确；审查必须核对具体字段、分支和原因。
   局部异常处理修复不要求取得正常单号数据或绕过拦截；补充覆盖按实际变更评估，不凭语法通过自动豁免，也不扩大到无关正常分支。
