<!-- prompt-version: 1.0.0 -->
## 时区与轨迹规则

1. 运输商返回的时间和时区使用 `common.time_utils.TimeUtils` 解析；轨迹时间字段使用 `DateTimeModel`，不换成随意格式的字符串。
2. 运输商未返回时区时，默认补充时区定义在 `framework/carrier_type/carrier_type.py` 的 `carrier_type` 中，优先运输商总部所在地时区。
3. 该文件属于共享层；本任务需要新增或修正默认时区时，列出 carrier、拟定时区、依据和文件，转人工，不在 carrier 内硬编码代替。
4. 事件有详细地点且需按地点补充时区时，通过 Gather 的 `update_config` 传入
   `framework.result_format.formatters.default_timezone_formatter.DefaultTimezoneFormatConfigModel(parse_event_timezone_by_loc=True)`。
   格式化器使用事件地址的国家、州和邮编推算时区；不要仅凭地点名称硬编码偏移。
5. 保留运输商返回的有效时区，不用默认时区覆盖；地点或时区不明确时不猜测。
6. `ResultFormatter` 顺序为默认值、国家、默认时区、事件顺序、EDD、状态、扩展状态、provider。
   保留 `update_config` 和 `forbid_sort` 等现有配置，不重复实现公共格式化。
7. 不修改与本轮错误无关的轨迹顺序、去重、地址、状态码、最终状态和请求参数。
8. 修复 `"Z"` 前先确认它在哪个字段：在 `createtime` 日期文本里，补 ISO 8601 format（如 `%Y-%m-%dT%H:%M:%S%z`）；在 `times` 时区文本里，`formats` 不参与时区解析，必须在 carrier 本地把 `"Z"` 映射为 `"+00:00"` 或使用 UTC 默认，不能只加 format 就声称修复。两种情况都必须给出 `"Z"` 样本的实际解析结果；只做静态语法检查不算验证。
