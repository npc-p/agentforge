# AgentForge 前端镜像（Next.js standalone）
#
# 关键点：BACKEND_ORIGIN 必须在**构建时**传入。next.config 的 rewrites 会把
# 目标地址写进构建产物，运行时再改环境变量是不生效的——这是最容易踩的坑。
FROM node:22-alpine AS deps
WORKDIR /app
# libc6-compat：Next 在 alpine 上需要 glibc 兼容层
RUN apk add --no-cache libc6-compat
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app
RUN apk add --no-cache libc6-compat
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# 项目没有 public/ 目录时创建空目录，避免下一阶段的 COPY 直接失败
RUN mkdir -p public
ARG BACKEND_ORIGIN=http://backend:8010
ENV BACKEND_ORIGIN=$BACKEND_ORIGIN
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
RUN apk add --no-cache libc6-compat
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# standalone 产物已包含运行所需的 node_modules 子集
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3000
CMD ["node", "server.js"]
