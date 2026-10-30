# 与根目录 Dockerfile 一致：基础镜像可换成内网 TCE 仓库，避免构建期直连 Docker Hub。
#   docker build --build-arg NODE_IMAGE=17track.tencentcloudcr.com/node/node:22-alpine \
#                --build-arg NGINX_IMAGE=17track.tencentcloudcr.com/nginx/nginx:1.28-alpine .
ARG NODE_IMAGE=node:22-alpine
ARG NGINX_IMAGE=nginx:1.28-alpine
ARG NPM_REGISTRY=https://registry.npmmirror.com

FROM ${NODE_IMAGE} AS build
WORKDIR /frontend
COPY package.json pnpm-lock.yaml ./
RUN npm install -g pnpm@11.28.5 --registry=${NPM_REGISTRY} \
    && pnpm install --frozen-lockfile --registry=${NPM_REGISTRY}
COPY . .
RUN pnpm build

FROM ${NGINX_IMAGE}
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /frontend/dist /usr/share/nginx/html
EXPOSE 8080
