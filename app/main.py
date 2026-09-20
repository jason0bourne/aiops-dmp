import hashlib
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session
from .config import settings
from .database import Alert, AuditLog, Base, DatabaseInstance, Remediation, engine, get_db
from .security import current_user, issue_token, require_admin, verify_webhook_token
from .monitoring import prometheus_status

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("orbit")


def seed(db: Session) -> None:
    if db.query(DatabaseInstance).count(): return
    db.add_all([
        DatabaseInstance(name="prod-mysql-02", engine="MySQL 8.0", service="订单中心", role="主库", utilization=78, connections=412),
        DatabaseInstance(name="pg-reporting-01", engine="PostgreSQL 15", service="数据仓库", role="只读", utilization=43, connections=86),
        DatabaseInstance(name="redis-cache-cluster", engine="Redis 7.2", service="缓存集群", role="6 节点", utilization=61, connections=0),
    ])
    db.add_all([
        Alert(severity="critical", title="数据库慢查询 P99 超过阈值", resource="prod-mysql-02 / 订单中心", status="processing", diagnosis="order_items 表缺少复合索引；建议低峰期审批后创建索引。"),
        Alert(severity="warning", title="API Gateway 连接池使用率偏高", resource="gateway-east-01 / 网关", status="open", diagnosis="建议将连接池扩容至 400 并观察 15 分钟。"),
        Alert(severity="info", title="备份任务耗时较昨日增加 18%", resource="pg-reporting-01 / 数据仓库", status="suppressed", diagnosis="趋势观察中。"),
    ])
    db.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.environment == "production" and (not settings.secret_key or not settings.admin_password):
        raise RuntimeError("SECRET_KEY and ADMIN_PASSWORD are mandatory in production")
    Path("data").mkdir(exist_ok=True)
    Base.metadata.create_all(bind=engine)
    db = next(get_db()); seed(db); db.close()
    yield


app = FastAPI(title="ORBIT AI Ops DMP", version="0.1.0", docs_url="/api/docs", redoc_url=None, lifespan=lifespan)
origins = [x.strip() for x in settings.cors_origins.split(",") if x.strip()]
if origins: app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["GET", "POST", "PATCH"], allow_headers=["Authorization", "Content-Type"])


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)

class AlertStatus(BaseModel):
    status: str = Field(pattern="^(open|processing|resolved|suppressed)$")

class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)

class AlertmanagerAlert(BaseModel):
    status: str = Field(pattern="^(firing|resolved)$")
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)

class AlertmanagerPayload(BaseModel):
    alerts: list[AlertmanagerAlert] = Field(min_length=1, max_length=100)

@app.get("/api/healthz", tags=["system"])
def healthz(): return {"status": "ok", "service": "orbit-api", "mode": settings.deployment_mode}

@app.post("/api/auth/token", tags=["auth"])
def login(input: Login):
    expected = settings.admin_password
    if not expected or not hashlib.sha256(input.username.encode()).digest() == hashlib.sha256(settings.admin_username.encode()).digest() or not hashlib.sha256(input.password.encode()).digest() == hashlib.sha256(expected.encode()).digest():
        raise HTTPException(401, "Invalid credentials")
    return {"access_token": issue_token(input.username, "admin"), "token_type": "bearer", "expires_in": 3600}

@app.get("/api/dashboard", tags=["operations"])
def dashboard(user: dict = Depends(current_user), db: Session = Depends(get_db)):
    alerts = db.query(Alert).filter(Alert.status.in_(["open", "processing"])).count()
    total = db.query(Remediation).count()
    succeeded = db.query(Remediation).filter(Remediation.status == "succeeded").count()
    automation_rate = round(succeeded / total * 100, 1) if total else 0
    return {"resources": 1284, "health": 99.96, "alerts": alerts, "auto_remediation": automation_rate, "trend": [42, 48, 45, 57, 53, 71, 61, 66, 58, 72, 68, 75]}

@app.get("/api/alerts", tags=["operations"])
def list_alerts(user: dict = Depends(current_user), db: Session = Depends(get_db)):
    return [{"id": a.id, "severity": a.severity, "title": a.title, "resource": a.resource, "status": a.status, "diagnosis": a.diagnosis, "created_at": a.created_at} for a in db.query(Alert).order_by(Alert.id).all()]

@app.patch("/api/alerts/{alert_id}", tags=["operations"])
def update_alert(alert_id: int, input: AlertStatus, user: dict = Depends(current_user), db: Session = Depends(get_db)):
    alert = db.get(Alert, alert_id)
    if not alert: raise HTTPException(404, "Alert not found")
    alert.status = input.status
    db.add(AuditLog(actor=user["sub"], action="alert.status.update", target=str(alert_id), detail=input.status)); db.commit()
    return {"id": alert.id, "status": alert.status}


REMEDIATION_CATALOG = {
    "create_missing_index": {
        "title": "创建缺失的复合索引",
        "rationale": "根据慢查询诊断，为 order_items 查询路径创建复合索引，降低 P99 延迟。",
    },
    "increase_connection_pool": {
        "title": "扩容 API Gateway 连接池",
        "rationale": "将连接池从当前配置扩容到 400，并在 15 分钟观察窗口内验证错误率。",
    },
    "rebuild_backup_statistics": {
        "title": "刷新备份统计信息",
        "rationale": "刷新数据仓库备份统计，避免计划器使用过期基线导致耗时异常。",
    },
}


def remediation_view(item: Remediation) -> dict:
    return {
        "id": item.id,
        "alert_id": item.alert_id,
        "action_key": item.action_key,
        "action_title": item.action_title,
        "rationale": item.rationale,
        "status": item.status,
        "requested_by": item.requested_by,
        "approved_by": item.approved_by,
        "result": item.result,
        "created_at": item.created_at,
        "executed_at": item.executed_at,
    }


def suggest_action(alert: Alert) -> str:
    title = f"{alert.title} {alert.diagnosis or ''}".lower()
    if "索引" in title or "slow" in title or "慢查询" in title:
        return "create_missing_index"
    if "连接池" in title or "connection pool" in title:
        return "increase_connection_pool"
    return "rebuild_backup_statistics"


@app.post("/api/alerts/{alert_id}/diagnose", tags=["remediation"])
def diagnose_alert(alert_id: int, user: dict = Depends(current_user), db: Session = Depends(get_db)):
    """Create an auditable, non-executable remediation proposal for an alert."""
    alert = db.get(Alert, alert_id)
    if not alert:
        raise HTTPException(404, "Alert not found")
    existing = db.query(Remediation).filter(
        Remediation.alert_id == alert_id,
        Remediation.status.in_(["pending_approval", "approved", "executing"]),
    ).first()
    if existing:
        return remediation_view(existing)
    action_key = suggest_action(alert)
    action = REMEDIATION_CATALOG[action_key]
    proposal = Remediation(
        alert_id=alert.id,
        action_key=action_key,
        action_title=action["title"],
        rationale=action["rationale"],
        requested_by=user["sub"],
    )
    alert.status = "processing"
    db.add(proposal)
    db.add(AuditLog(actor=user["sub"], action="remediation.proposed", target=str(alert.id), detail=action_key))
    db.commit()
    db.refresh(proposal)
    return remediation_view(proposal)


@app.get("/api/remediations", tags=["remediation"])
def list_remediations(user: dict = Depends(current_user), db: Session = Depends(get_db)):
    return [remediation_view(x) for x in db.query(Remediation).order_by(Remediation.id.desc()).limit(100).all()]


@app.post("/api/remediations/{remediation_id}/approve", tags=["remediation"])
def approve_remediation(remediation_id: int, user: dict = Depends(require_admin), db: Session = Depends(get_db)):
    item = db.get(Remediation, remediation_id)
    if not item:
        raise HTTPException(404, "Remediation not found")
    if item.status != "pending_approval":
        raise HTTPException(409, f"Remediation is {item.status}")
    item.status = "approved"
    item.approved_by = user["sub"]
    db.add(AuditLog(actor=user["sub"], action="remediation.approved", target=str(item.id), detail=item.action_key))
    db.commit()
    return remediation_view(item)


@app.post("/api/remediations/{remediation_id}/execute", tags=["remediation"])
def execute_remediation(remediation_id: int, user: dict = Depends(require_admin), db: Session = Depends(get_db)):
    """Execute only catalog actions; the MVP executor updates state without shell/database writes."""
    item = db.get(Remediation, remediation_id)
    if not item:
        raise HTTPException(404, "Remediation not found")
    if item.status != "approved":
        raise HTTPException(409, "Remediation must be approved before execution")
    alert = db.get(Alert, item.alert_id)
    if not alert:
        raise HTTPException(409, "Remediation alert no longer exists")
    item.status = "executing"
    db.add(AuditLog(actor=user["sub"], action="remediation.execution.started", target=str(item.id), detail=item.action_key))
    db.commit()
    item.status = "succeeded"
    item.result = "安全演练执行完成；已更新处置状态。接入生产执行器前需替换此适配器并保留同等审计与回滚能力。"
    item.executed_at = func.now()
    alert.status = "resolved"
    db.add(AuditLog(actor=user["sub"], action="remediation.execution.succeeded", target=str(item.id), detail=item.result))
    db.commit()
    db.refresh(item)
    return remediation_view(item)

@app.get("/api/databases", tags=["database"])
def databases(user: dict = Depends(current_user), db: Session = Depends(get_db)):
    return [{"id": x.id, "name": x.name, "engine": x.engine, "service": x.service, "role": x.role, "healthy": x.healthy, "utilization": x.utilization, "connections": x.connections} for x in db.query(DatabaseInstance).all()]

@app.post("/api/ai/ask", tags=["ai"])
def ask_ai(input: AskRequest, user: dict = Depends(current_user), db: Session = Depends(get_db)):
    db.add(AuditLog(actor=user["sub"], action="ai.ask", target="operations-assistant", detail=input.question[:500])); db.commit()
    return {"answer": "已创建受审计的 AI 分析请求。生产接入时，请将此端点连接到经审批的模型网关，并在工具执行前保留人工确认。", "requires_approval": True}

@app.get("/api/audit", tags=["security"])
def audit(user: dict = Depends(require_admin), db: Session = Depends(get_db)):
    return [{"actor": x.actor, "action": x.action, "target": x.target, "detail": x.detail, "created_at": x.created_at} for x in db.query(AuditLog).order_by(AuditLog.id.desc()).limit(100)]

@app.get("/api/integrations/prometheus/status", tags=["integrations"])
def prometheus_integration_status(user: dict = Depends(current_user), db: Session = Depends(get_db)):
    result = prometheus_status()
    db.add(AuditLog(actor=user["sub"], action="integration.prometheus.status.read", target="prometheus", detail=str(result.get("reachable"))))
    db.commit()
    return result

@app.post("/api/integrations/alertmanager/webhook", status_code=202, tags=["integrations"])
def receive_alertmanager(payload: AlertmanagerPayload, x_orbit_webhook_token: str | None = Header(default=None), db: Session = Depends(get_db)):
    """Alertmanager-compatible inbound webhook. It only records alerts in ORBIT."""
    verify_webhook_token(x_orbit_webhook_token)
    accepted = 0
    for incoming in payload.alerts:
        labels, annotations = incoming.labels, incoming.annotations
        title = annotations.get("summary") or labels.get("alertname") or "Alertmanager alert"
        resource = labels.get("instance") or labels.get("job") or labels.get("service") or "unknown resource"
        severity = labels.get("severity", "warning").lower()
        severity = severity if severity in {"critical", "warning", "info"} else "warning"
        status = "resolved" if incoming.status == "resolved" else "open"
        db.add(Alert(severity=severity, title=title[:256], resource=resource[:256], status=status, diagnosis=annotations.get("description", "")[:2000]))
        accepted += 1
    db.add(AuditLog(actor="alertmanager", action="alertmanager.webhook.receive", target="alerts", detail=f"accepted={accepted}"))
    db.commit()
    return {"accepted": accepted, "mode": "read-only pilot"}

static = Path(__file__).resolve().parent.parent / "static"
app.mount("/assets", StaticFiles(directory=static), name="assets")
@app.get("/", include_in_schema=False)
def home(): return FileResponse(static / "index.html")
