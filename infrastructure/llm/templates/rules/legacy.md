<!-- prompt-version: 1.1.0 -->
## 旧框架与桥接规则

1. `LegacySpiderGather` / `LegacySpiderProvider.get_plugin(...)` 是桥接，实际业务可能在同 carrier 的 `spiders/`，不要在包装器重复实现解析。
2. 旧插件使用 `id_dict/proxy/timeout/index_html`；方法为 `await spider.read_html()`、`await spider.process_html()`，不强加 `track_model` 参数。
3. 桥接将新 `param` 转为旧 `parm`；保持字段和批量单号关联。
4. 字典返回沿用 `common.data_module.response_data`，保留 `code/data/s_count/f_count/message/traceback/error_type`。
5. 桥接将字符串 `ServiceError/WebError/ProcessError` 转为对应错误；将 `NoDataError/NoDataInvalidNumberError` 转为成功的 NotFound。
   所以旧框架这两种无单返回也必须满足上述 NotFound 规则，拦截页禁止使用。
6. 部分旧实现已有 `raise ErrorType.ServiceError.error(...)`；保留目标方法采用的错误协议，不统一迁移旧插件。
7. 旧框架实现只读定位。引用旧 Provider、导入、插件初始化或历史注释不证明本次解析委托旧实现；沿当前请求核对实际调用。独立 gathers 分支的本地缺陷允许在授权目录修复。禁止修改 spiders 文件，也禁止在 gathers 包装器复制旧插件逻辑绕过只读范围。共享 provider 或基类需要变更时转人工。
