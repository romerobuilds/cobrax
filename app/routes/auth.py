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

    # ===== DEBUG (PASSO 1) =====
    senha = payload.senha
    print("======= DEBUG REGISTER =======")
    print("SENHA (início):", repr(senha[:20]))
    print("LEN (chars):", len(senha))
    print("LEN (bytes):", len(senha.encode("utf-8")))
    print("==============================")

    existe = db.query(User).filter(User.email == payload.email).first()
    if existe:
        raise HTTPException(status_code=400, detail="Email já cadastrado")

    user = User(
        email=payload.email,
        nome=payload.nome,
        senha_hash=hash_senha(senha),
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return UserPublic(
        id=user.id,
        email=user.email,
        nome=user.nome
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

    token = criar_token({"sub": str(user.id), "email": user.email})
    return Token(access_token=token)
