"""POST /api/auth/login, POST /api/auth/change-password."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.auth import create_access_token, hash_password, verify_password
from backend.deps import get_current_user, get_db
from backend.limiter import limiter
from backend.models import User
from backend.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    UserInfo,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user_info(u: User) -> UserInfo:
    return UserInfo(
        id=u.id,
        username=u.username,
        is_admin=u.is_admin,
        must_change_password=u.must_change_password,
        allowed_sources=u.allowed_sources,
    )


@router.post("/login", response_model=LoginResponse)
@limiter.limit("30/minute")
def login(request: Request, req: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    user = db.execute(select(User).where(User.username == req.username)).scalar_one_or_none()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad username or password")
    token = create_access_token(subject=str(user.id))
    return LoginResponse(access_token=token, user=_user_info(user))


@router.get("/me", response_model=UserInfo)
def me(user: User = Depends(get_current_user)) -> UserInfo:
    return _user_info(user)


@router.post("/change-password", response_model=UserInfo)
@limiter.limit("30/minute")
def change_password(
    request: Request,
    req: ChangePasswordRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> UserInfo:
    if not verify_password(req.old_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Old password incorrect")
    user.password_hash = hash_password(req.new_password)
    user.must_change_password = False
    db.commit()
    db.refresh(user)
    return _user_info(user)
