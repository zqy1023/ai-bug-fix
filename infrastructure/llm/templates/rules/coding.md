<!-- prompt-version: 2.0.0 -->
## 编码规则

1. Python 使用 4 空格缩进；类名 PascalCase，方法和变量 snake_case；导入沿用 `framework.*`、`common.*`。
2. 保持目标文件的引号、编码、换行、注释及类型标注习惯，不批量补类型或改名。
3. 保持 `async def` / `await`，不插入同步网络调用或阻塞等待。
4. 使用现有客户端、解析器和 `common.log_utils.LogUtils`。HTML/XML 使用现有解析器或 lxml，不用正则提取业务字段。
5. 不复制裸 `except`、吞异常和敏感日志；不硬编码凭据、单号、响应样本或环境地址。
6. 只保护本次错误涉及的字段和分支，不给所有假设异常添加默认值。
7. 禁止为本轮修复新增辅助函数：包括私有方法、模块函数、嵌套函数；`_check_blocked`、`is_blocked`、`validate_response` 等同样禁止。
   在已有业务方法的出错位置局部处理，不抽取函数，不新增通用拦截器；新增 helper 会被门禁退回。
   必要的框架入口覆写仅限 `__init__/read_html/process_html/on_finish`，不能借入口覆写包装新增 helper。
