# ORBIT AI Ops DMP

面向云资源、告警与数据库资产的 AI 运维控制台 MVP。项目提供经过身份验证的运维 API、审计日志、告警处置界面与可容器化部署配置。

> 此仓库是可部署的 MVP，而非可直接连接任意生产数据库的“万能管控器”。接入真实环境前必须完成 SSO、凭据托管、网络隔离、审批流和安全评估。

## 已实现

- JWT 风格 HMAC 签名登录令牌，管理员角色保护与 1 小时过期
- 运维总览、告警读取/处置、数据库资产展示、AI 分析请求和审计日志 API
- FastAPI OpenAPI 文档：`/api/docs`
- 非 root 容器、健康检查、只读应用文件系统、PostgreSQL Compose 配置
- Kubernetes Deployment/Service 示例与 GitHub Actions CI
- 单租户试点集成：只读 Prometheus 连通性检查与 Alertmanager Webhook 告警接入

## 本地运行

需要 Python 3.12+。开发环境使用 SQLite：

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export ADMIN_PASSWORD='change-this-now'
uvicorn app.main:app --reload
```

访问 `http://localhost:8000`，使用 `admin` 和 `ADMIN_PASSWORD` 的值登录。

## Docker Compose 部署

```bash
cp .env.example .env
# 生成随机密钥并编辑 .env；不要使用示例密码
docker compose up -d --build
curl http://localhost:8080/api/healthz
```

将 `CORS_ORIGINS` 显式设为对外控制台域名。生产环境务必将 PostgreSQL 更换为受管、高可用数据库，使用密钥管理服务注入环境变量，并在反向代理/WAF 后以 HTTPS 提供服务。

## 单租户试点接入

平台不会对 Prometheus 或受监控数据库执行写操作。将下列值放入 `.env` 后重建 `orbit` 服务：

```env
DEPLOYMENT_MODE=pilot
PROMETHEUS_URL=https://prometheus.example.internal
PROMETHEUS_BEARER_TOKEN=optional-read-only-token
ALERTMANAGER_WEBHOOK_TOKEN=long-random-shared-secret
```

使用已登录的账号访问 `GET /api/integrations/prometheus/status` 可验证连通性。Alertmanager 将 Webhook 指向：

```text
POST https://your-orbit-host/api/integrations/alertmanager/webhook
X-ORBIT-WEBHOOK-TOKEN: <ALERTMANAGER_WEBHOOK_TOKEN>
```

Webhook 仅记录入站告警；ORBIT 绝不会从该接口向监控系统回写配置或执行自动修复。

## Kubernetes

1. 构建并推送镜像到受控镜像仓库，替换 `deploy/k8s/orbit.yaml` 中的镜像地址。
2. 创建密钥（`SECRET_KEY`、`ADMIN_PASSWORD`、`DATABASE_URL`），不要将其写入 Git：

```bash
kubectl create secret generic orbit-secrets \
  --from-literal=SECRET_KEY='...' \
  --from-literal=ADMIN_PASSWORD='...' \
  --from-literal=DATABASE_URL='postgresql+psycopg://...'
kubectl apply -f deploy/k8s/orbit.yaml
```

应用部署前，请添加 Ingress、TLS 证书、NetworkPolicy、PodDisruptionBudget、集中日志与备份策略。

## 生产接入边界

- 使用企业 OIDC/SSO 替换内置管理员密码登录；内置认证仅适用于受控 MVP 环境。
- 数据库操作使用最小权限、短期凭据和堡垒网络；不得在浏览器或代码中保存数据库密码。
- AI 端点仅记录请求并返回受控提示。接入模型/工具之前，需实现敏感数据脱敏、工具白名单、审批和不可篡改审计。
- `Base.metadata.create_all` 便于 MVP 启动；正式演进需引入 Alembic 迁移与备份/恢复演练。

## 许可证

MIT
