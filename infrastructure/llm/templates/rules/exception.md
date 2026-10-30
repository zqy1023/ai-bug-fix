<!-- prompt-version: 1.1.0 -->
## 异常规则

1. 解析部分只使用 `ServiceError` 和 `ProcessError`，不新增公共异常类型。
2. 通常使用 `ServiceError + 简短原因`。开发初期无法确认具体原因、需要堆栈排查时使用 `ProcessError`；它会打印堆栈。
3. 抛出方式：`raise ErrorType.ServiceError.error("简短原因")`。
4. 返回方式：`return TrackResponseModel.on_all_errors(ErrorType.ServiceError, track_model, error_msg="简短原因")`。
5. 上述两种方式可将 `ServiceError` 替换为 `ProcessError`；沿用目标方法已有处理方式。
6. `ErrorType.X.error(...)` 返回 `StateError`，必须 `raise`；不能只调用后继续返回成功。
7. 错误信息不要含随单号变化的内容。`not find shipment or parcel with reference <单号>` 应去掉单号，只留固定原因。
8. 已有 HTTP 状态码时写入原因，例如 `HTTP 403: access denied`；不知道状态码时不编造。
9. 不输出完整单号、Token、Cookie、认证信息、请求或响应全文，不把原始异常文本直接拼入错误信息。
10. 请求阶段沿用框架已有的网络 `WebError`、执行超时 `TimeoutError` 和取消机制；不要在解析层重新包装它们。
11. 不用裸 Exception、空列表、默认对象、静默跳过或成功返回掩盖异常；不因字段缺失就认定所有字段都必填。
12. 已收到明确的服务错误回执，应在正常解析前按 ServiceError 处理并保留简短原因。服务器错误状态、完整的服务不可用错误页或明确业务错误字段，须与本次响应及当前分支对应；不是仅判断是否有 HTML 或全文是否出现某个关键词。不能因为根因是上游故障就保留已知错误页导致的 JSONDecodeError，也不能把未知解码失败或无响应一律转 ServiceError。
