from fastapi import APIRouter
from pydantic import BaseModel
from uuid import uuid4
from fastapi import Depends
from app.core.deps import get_current_user
from app.models.user import User

router = APIRouter(prefix="/empresas", tags=["Empresas"])

# Simulando banco por enquanto (em memória)
empresas_db = []

class EmpresaCreate(BaseModel):
    nome: str
    cnpj: str
    email: str
    

@router.post("/")
def criar_empresa(empresa: EmpresaCreate, current_user: User = Depends(get_current_user)):
    nova_empresa = {
        "id": str(uuid4()),
        "nome": empresa.nome,
        "cnpj": empresa.cnpj,
        "email": empresa.email
    }

    empresas_db.append(nova_empresa)

    return {
        "message": "Empresa criada com sucesso",
        "empresa": nova_empresa
    }
