from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.database_.database import get_db
from app.models.user import User
from app.schemas.auth import UserCreate, UserPublic, Token
from app.core.security import hash_senha, verificar_senha
from app.core.jwt import criar_token

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/register", response_model=UserPublic, status_code=201)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    senha = payload.senha

    existe = db.query(User).filter(User.email == payload.email).first()
    if existe:
        raise HTTPException(status_code=400, detail="Email já cadastrado")

    user = User(
        email=payload.email,
        nome=payload.nome,
        senha_hash=hash_senha(senha),
        is_master=True,  # ✅ mantém compatibilidade com o que já existe hoje
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return UserPublic(
        id=user.id,
        email=user.email,
        nome=user.nome,
        is_master=user.is_master,
    )


@router.post("/login", response_model=Token)
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.email == form.username).first()

    if not user or not verificar_senha(form.password, user.senha_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciais inválidas"
        )

    token = criar_token({
        "sub": str(user.id),
        "email": user.email,
        "is_master": bool(user.is_master),
    })

    return Token(access_token=token)