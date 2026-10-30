# 基础镜像默认走国内 DaoCloud 公共镜像源（非 slim）：构建期不必直连 Docker Hub，
# 同时保留 git、gcc/make、ca-certificates、passwd，覆盖运行期需要（GitPython 提交、
# 依赖编译、useradd）。
# 如该镜像源不可用，可覆盖为其他国内源或官方源：
#   --build-arg PYTHON_IMAGE=docker.1ms.run/library/python:3.11-bullseye
#   --build-arg PYTHON_IMAGE=mirror.ccs.tencentyun.com/library/python:3.11-bullseye
#   --build-arg PYTHON_IMAGE=docker.io/library/python:3.11-bullseye
#   --build-arg PYTHON_IMAGE=17track.tencentcloudcr.com/python/python:3.11-bullseye
# 版本下限是 3.11：infrastructure/llm/session.py 用了 asyncio.timeout（3.11 才有），
# 换成 3.10 只会在运行期抛 AttributeError，构建期发现不了。
# FROM 之前只能放注释或全局 ARG。
ARG PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.11-bullseye
FROM ${PYTHON_IMAGE} AS build
# Bullseye security 仓库在当前基础镜像环境中已不可用；构建只依赖官方
# bullseye 主仓库和 updates，移除 security 源避免过期包索引导致 404。
RUN find /etc/apt -type f \( -name '*.list' -o -name '*.sources' \) -exec \
    sed -i '/debian-security/d' {} + && \
    printf 'Acquire::Check-Valid-Until "false";\n' > /etc/apt/apt.conf.d/99no-check-valid-until

# 安装 Node.js 20
# 脚本会添加 NodeSource 的 APT 软件源到系统中，并安装 Node.js 20
# 显式启用 pipefail，NodeSource 脚本失败时不能静默继续并退回 Debian 自带 Node.js。
RUN bash -o pipefail -c 'curl -fsSL https://deb.nodesource.com/setup_20.x | bash -'
# NodeSource 的 nodejs 包通常自带 npm；只有缺失时才从 Debian 补装，避免无意间选择旧版 Node.js/npm。
RUN apt-get update -o Acquire::Check-Valid-Until=false && apt-get install -y --no-install-recommends nodejs && \
    (command -v npm >/dev/null || apt-get install -y npm) && \
    node --version && npm --version
# npm 11 支持 Node.js 20；npm@latest 已升级到仅支持 Node.js 22+ 的 npm 12。
RUN npm install -g npm@11

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt ./requirements.txt
# yq_common 通过内网 Nexus（仅 HTTP，https 握手实测失败）获取，wheel 已在
# requirements.txt 中用 sha256 内容哈希锁定。TLS 需等 Nexus 侧升级后才能真正启用；
# 在此之前不得删除 --trusted-host n.17track.net，否则 HTTP 源会因证书校验被拒绝。
# 不要改为 --require-hashes：那会要求全部传递依赖也提供哈希，当前仓库无法满足。
# 构建机没有可用外网代理时，直连 pypi.org 会超时，因此 index 默认走清华源（与内网其它
# 服务的构建方式一致）。yq_common 在 requirements.txt 里是直接 URL 依赖，不走 index，
# 仍然必须保留 --trusted-host n.17track.net。
ARG PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_INDEX_HOST=pypi.tuna.tsinghua.edu.cn
RUN python -m pip install --upgrade pip -i ${PIP_INDEX} --trusted-host ${PIP_INDEX_HOST} \
    && python -m pip install -r requirements.txt -i ${PIP_INDEX} \
        --trusted-host ${PIP_INDEX_HOST} --trusted-host n.17track.net
# 前端：vite 构建产物落到 frontend/dist，由 server.py 直接托管（单容器、单端口 8000）。
# prebuild 钩子会执行 `python -m workflow.engine.handoff` 生成契约文件；handoff 会
# 导入 workflow.engine.graph / server.projections，因此 config、core、infrastructure、
# agents、server、workflow 必须在 pnpm build 之前全部就位，不能只复制 workflow/。
COPY workflow ./workflow
COPY core ./core
COPY infrastructure ./infrastructure
COPY agents ./agents
COPY server ./server
COPY config ./config
COPY frontend/package.json frontend/pnpm-lock.yaml ./frontend/
ARG NPM_REGISTRY=https://registry.npmmirror.com
RUN npm install -g pnpm@11.28.5 \
    && cd frontend && pnpm install --frozen-lockfile --registry=${NPM_REGISTRY}
COPY frontend ./frontend
RUN cd frontend && pnpm build && rm -rf node_modules

# workbench merged into server
COPY server.py ./server.py
COPY __init__.py ./__init__.py
# uid 10001 与 docker-compose 的 APP_UID 默认值一致；HOME 必须显式指向 app 用户的
# 主目录——compose 以数字 user 运行时容器不会自动设置 HOME，git 提交、httpx 等
# 依赖会退回 "/" 或无主目录而失败。
# /var/log/yqtrack 必须在镜像里建好并授权：yq_common 的日志初始化在 Linux 下
# 固定写 /var/log/yqtrack/<project>，它吞掉 makedirs 的 PermissionError 后，
# RotatingFileHandler 会因目录不存在在 import 期抛 FileNotFoundError，
# api 容器因此崩溃重启、frontend 的 depends_on: service_healthy 永不满足。
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /home/app /app/runtime /var/log/yqtrack/py.ai.bug.fixer \
    && chown -R app:app /app /home/app /var/log/yqtrack
ENV HOME=/home/app
USER app
EXPOSE 8000
# --serve-only：只起 API，不在容器启动时自动开一次修复流程（批次由 POST /api/v1/runs
# 触发）。原 CMD 是裸 `python server.py`，会跑完一次流程就退出，与 compose 的健康检查
# 和常驻服务语义冲突；末尾多余的 "" 还会被 argparse 判为非法参数。
CMD ["python", "server.py", "--serve-only"]
