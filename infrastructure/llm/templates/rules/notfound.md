<!-- prompt-version: 1.2.0 -->
## NotFound 规则

1. 只有当前分支的明确业务字段或已确认业务契约支持查无结果时，才允许返回 NotFound；无法区分无单与异常时保留错误并记录缺口。
2. 必须有明确业务字段及字段值证明单号无效、不存在或无轨迹。
   例如解析 JSON 后判断 `error` 字段明确表示"无效单号"，才允许 NotFound；不能只搜索响应全文。
3. `{"track_list": []}` 只能说明列表为空，无法准确区分数据 NotFound，不能直接返回 NotFound。
   `data=null`、缺字段、解析失败、空响应也不能直接判无单。
4. HTML 必须定位具体元素标签，再判断该元素内容是否明确表示无单；禁止响应全文包含某段文字就判无单。
5. 新框架使用 `TrackResultModel.not_found(...)`，作为业务结果返回；不存在 `ErrorType.NotFound` 或 `ErrorType.NoDataError`。
6. 保留目标 carrier 已确认的业务状态映射，不把服务错误、登录失败、验证码或拦截页归成 NotFound。
7. `status=success` 仅证明接口执行成功，不证明单号不存在或业务上无轨迹。其他分支已有 not_found 不构成当前分支的语义依据。
8. 禁止仅为消除 `no events` 等异常将原报错出口改成成功项 + not_found。没有明确字段或已确认业务契约时，保留报错并说明缺口；修复与审查都必须独立核对依据。
