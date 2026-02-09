from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from uuid import uuid4
import hashlib

router = APIRouter(prefix="/usuarios", tags=["Usuários"])

# Simulando banco
usuarios_db = []

class UsuarioCreate(BaseModel):
    nome: str
    email: EmailStr
    senha: str
    empresa_id: str
    role: str = "admin"

@router.post("/")
def criar_usuario(usuario: UsuarioCreate):
    # Verificar se email já existe
    for u in usuarios_db:
        if u["email"] == usuario.email:
            raise HTTPException(status_code=400, detail="Email já cadastrado")

    senha_hash = hashlib.sha256(usuario.senha.encode()).hexdigest()

    novo_usuario = {
        "id": str(uuid4()),
        "nome": usuario.nome,
        "email": usuario.email,
        "senha": senha_hash,
        "empresa_id": usuario.empresa_id,
        "role": usuario.role
    }

    usuarios_db.append(novo_usuario)

    return {
        "message": "Usuário criado com sucesso",
        "usuario": {
            "id": novo_usuario["id"],
            "nome": novo_usuario["nome"],
            "email": novo_usuario["email"],
            "empresa_id": novo_usuario["empresa_id"],
            "role": novo_usuario["role"]
        }
    }
