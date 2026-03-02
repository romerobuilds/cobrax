import app.models  # força carregar tudo antes do ORM ser usado
from fastapi import FastAPI
from app.database_.database import Base, engine
from app.routers.usuarios import router as usuarios_router
from app.routes.auth import router as auth_router
from app.routes.company import router as company_router
from app.routers.health import router as health_router
from app.routes.client import router as client_router
from app.routes.email_template import router as templates_router
from app.models import user, company, client, email_template
from app.routes.email_log import router as email_log_router
from app.routes.email_send import router as email_send_router
from app.routes import email_send_bulk
from app.routes.email_admin import router as email_admin_router
from app.routes.plan_admin import router as plan_admin_router
from app.routes.worker_status import router as worker_status_router
from fastapi.middleware.cors import CORSMiddleware
from app.routers.campaign import router as campaigns_router
from app.routers.campaign import router as campaign_router
from app.routes.dashboard import router as dashboard_router
import app.models

app = FastAPI(
    title="COBRAX",
    description="Sistema de automação de cobranças",
    version="0.1.0",
)

origins = [
    "http://95.216.138.163",
    "http://95.216.138.163:5173",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



# @app.on_event("startup")
# def startup():
#     Base.metadata.create_all(bind=engine)

app.include_router(health_router)
app.include_router(company_router)
app.include_router(usuarios_router)
app.include_router(auth_router)
app.include_router(client_router)
app.include_router(templates_router)
app.include_router(email_log_router)
app.include_router(email_send_router)
app.include_router(email_send_bulk.router)
app.include_router(email_admin_router)
app.include_router(plan_admin_router)
app.include_router(worker_status_router)
app.include_router(campaigns_router)
app.include_router(campaign_router)
app.include_router(dashboard_router)


