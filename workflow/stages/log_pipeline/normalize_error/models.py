"""规范异常日志步骤的输入输出模型。"""

from __future__ import annotations


from pydantic import Field

from core.contracts import FrozenModel


class NormalizeErrorInput(FrozenModel):
    """只从当前真实执行记录的查询条件读取日志。"""

    # run_id：字段说明见该模型文档与工作流契约。
    run_id: str = Field(min_length=1)


class NormalizedLog(FrozenModel):
    """一条标准化记录，保留错误原文和源数组位置。"""

    # source_index：字段说明见该模型文档与工作流契约。
    source_index: int = Field(ge=0)
    # service_name：字段说明见该模型文档与工作流契约。
    service_name: str = Field(min_length=1)
    # biztype：字段说明见该模型文档与工作流契约。
    biztype: str = Field(min_length=1)
    # carrier：字段说明见该模型文档与工作流契约。
    carrier: str = Field(min_length=1)
    # req：字段说明见该模型文档与工作流契约。
    req: str = Field(min_length=1)
    response: str = Field(default="")  # 可选，用于 response 重放验证
    track_config: str = Field(default="")  # 可选，用于 response 重放验证
    # raw_error：字段说明见该模型文档与工作流契约。
    raw_error: str
    message: str = ""
    # normalized_error：规范化后的异常文本（去行列号/对象地址，保留类名与时间值）。
    normalized_error: str = Field(min_length=1)
    # message_format：日志载体格式 req / model / requestResponse / trace。
    message_format: str = ""
    # exception_class：结构化异常类名；非异常文本（如业务主题）为空。
    exception_class: str = ""
    # exception_message：去掉类名与位置后的异常消息。
    exception_message: str = ""
    # frame：异常位置，形如 "tpcindia.py:41:read_html"，无位置为空。
    frame: str = ""
    # field：异常指向的业务字段路径，如 "track_result_events.datetime"。
    # 不是代码位置，但能缩小搜索范围，用于预算降档与领域规则选择。
    field: str = ""
    # response_kind：响应正文类型 json / xml / html / text / empty。
    response_kind: str = ""
    # blame：缺陷归属初判 transport / business / upstream / code / undetermined。
    # 只用于清洗分流与排序，不替代重放归因；undetermined 表示"判不出归属"，
    # 与 carrier（运输商）无关。
    blame: str = ""


class NormalizeErrorOutput(FrozenModel):
    """仅记录处理计数。代表问题写入 run 级产物 ``artifacts/normalized_records.json``。"""

    # run_id：字段说明见该模型文档与工作流契约。
    run_id: str = Field(min_length=1)
    # record_count：字段说明见该模型文档与工作流契约。
    record_count: int = Field(ge=0)
    # es_hits：字段说明见该模型文档与工作流契约。
    es_hits: int = Field(ge=0)
    # unparseable：字段说明见该模型文档与工作流契约。
    unparseable: int = Field(ge=0)
    # ignored_count：字段说明见该模型文档与工作流契约。
    ignored_count: int = Field(ge=0)
    # duplicate_count：字段说明见该模型文档与工作流契约。
    duplicate_count: int = Field(ge=0)
    # unique_count：字段说明见该模型文档与工作流契约。
    unique_count: int = Field(ge=0)
    record_limit: int = 0
    truncated: bool = False


__all__ = ["NormalizeErrorInput", "NormalizeErrorOutput", "NormalizedLog"]
