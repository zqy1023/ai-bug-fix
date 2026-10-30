<!-- prompt-version: 1.0.0 -->
## 新框架接口

1. 保留 `@plugins.register`、类名、继承关系和方法签名：
   - `async read_html(self, track_model: TrackModel) -> str`
   - `async process_html(self, track_model: TrackModel) -> TrackResponseModel`
2. 执行顺序：已有 `track_model.response` 则直接解析；否则 `read_html` → 保存 response → `process_html` → `ResultFormatter` → `on_finish`。
3. 拦截识别必须覆盖直接传入 response 的解析路径，不能只放在请求阶段。
4. 新请求字段为 `number/param/user_token/language/gather_channel`；不要把旧字段 `parm` 用到新模型。
5. 沿用 `ClientUtils.get_client(..., track_model=track_model)` 的异步上下文及既有代理、会话、客户端类型。
6. `TrackRequestConfigModel.timeout` 实际单位为毫秒，`timeout_second()` 除以 1000，不按错误注释改单位。
7. 返回 `TrackResponseModel` / `TrackResponseItemModel`，轨迹使用 `TrackResultModel` / `TrackResultEventModel`。
8. `on_single_succeed(track_model, result)` 只支持单请求；批量返回逐个关联 `request_item`，不复制首单结果。
9. 失败 item 不携带 `track_result`；成功 item 必须有结果模型，否则框架会改为 `ProcessError`。
