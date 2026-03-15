from uuid import UUID

from fastapi import Depends, HTTPException, Path, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.database_.database import get_db
from app.core.jwt import verificar_token
from app.models.user import User
from app.models.company import Company
from app.models.company_user import CompanyUser

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    payload = verificar_token(token)

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido",
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário não encontrado",
        )

    return user


def get_company_for_current_user(
    company_id: UUID = Path(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Company:
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Empresa não encontrada",
        )

    if user.is_master:
        if str(company.owner_id) == str(user.id):
            return company

        membership = (
            db.query(CompanyUser)
            .filter(
                CompanyUser.company_id == company.id,
                CompanyUser.user_id == user.id,
                CompanyUser.is_active.is_(True),
            )
            .first()
        )
        if membership:
            return company

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sem acesso a esta empresa",
        )

    membership = (
        db.query(CompanyUser)
        .filter(
            CompanyUser.company_id == company.id,
            CompanyUser.user_id == user.id,
            CompanyUser.is_active.is_(True),
        )
        .first()
    )
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sem acesso a esta empresa",
        )

    return company