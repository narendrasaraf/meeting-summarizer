from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.core.auth import get_password_hash, verify_password, create_access_token
from app.models.db import User, get_session
from app.models.schemas import UserRegister, UserLogin, TokenResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=201)
def register_user(
    payload: UserRegister,
    session: Session = Depends(get_session),
):
    # Check if user already exists
    existing = session.exec(select(User).where(User.email == payload.email)).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail="Email already registered",
        )

    # Create new user
    hashed = get_password_hash(payload.password)
    user = User(email=payload.email, hashed_password=hashed)
    session.add(user)
    session.commit()
    session.refresh(user)

    # Generate token
    token = create_access_token(data={"sub": user.email})
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
def login_user(
    payload: UserLogin,
    session: Session = Depends(get_session),
):
    # Authenticate user
    user = session.exec(select(User).where(User.email == payload.email)).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=400,
            detail="Incorrect email or password",
        )

    # Generate token
    token = create_access_token(data={"sub": user.email})
    return TokenResponse(access_token=token)
