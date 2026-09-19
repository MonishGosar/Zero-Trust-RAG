"""Verified identities, sessions and the shared tenant AND ACL policy."""

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from backend.models import AccessPolicy

ROLES = {"admin", "employee", "finance", "hr", "engineering"}
COOKIE = "folio_session"


class UserContext(BaseModel):
    user_id: str
    email: str
    tenant_id: str
    roles: list[str]


class Credentials(BaseModel):
    model_config = {"extra": "forbid"}
    email: str = Field(min_length=3, max_length=200, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    password: str = Field(min_length=12, max_length=128)


class Setup(Credentials):
    tenant_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")


class NewUser(Credentials):
    roles: list[Literal["admin", "employee", "finance", "hr", "engineering"]] = Field(
        default_factory=lambda: ["employee"], min_length=1, max_length=5
    )


class ACLUpdate(BaseModel):
    model_config = {"extra": "forbid"}
    allowed_roles: list[Literal["admin", "employee", "finance", "hr", "engineering"]] = Field(
        default_factory=list, max_length=5
    )
    allowed_users: list[str] = Field(default_factory=list, max_length=100)
    classification: Literal["internal", "confidential", "restricted"] = "internal"


def can_read(user: UserContext, doc: AccessPolicy) -> bool:
    return bool(
        doc.tenant_id
        and doc.tenant_id == user.tenant_id
        and (
            doc.owner_id == user.user_id
            or user.user_id in doc.allowed_users
            or set(user.roles).intersection(doc.allowed_roles)
        )
    )


def password_hash(password: str, salt: str) -> str:
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


class Security:
    def __init__(self, catalog, settings):
        self.catalog = catalog
        configured = settings.auth_jwt_secret.get_secret_value()
        if configured and len(configured) < 32:
            raise ValueError("AUTH_JWT_SECRET must contain at least 32 characters.")
        # Without an environment key, restarting intentionally invalidates all sessions.
        self.key = configured or secrets.token_urlsafe(48)
        self.settings = settings
        with catalog.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, tenant TEXT NOT NULL,
                    roles TEXT NOT NULL, salt TEXT NOT NULL, password_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id TEXT PRIMARY KEY, tenant TEXT NOT NULL, user_id TEXT NOT NULL,
                    event TEXT NOT NULL, created_at TEXT NOT NULL, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS login_attempts (
                    key TEXT PRIMARY KEY, attempts INTEGER NOT NULL, since INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS security_metadata (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
            """)

    def configured(self):
        with self.catalog.connect() as db:
            return bool(db.execute("SELECT 1 FROM users LIMIT 1").fetchone())

    def add_user(self, body, tenant, roles, first=False):
        salt = secrets.token_hex(16)
        digest = password_hash(body.password, salt)
        user = UserContext(
            user_id=str(uuid4()),
            email=body.email.lower(),
            tenant_id=tenant,
            roles=sorted(set(roles)),
        )
        with self.catalog.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if first and db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                raise HTTPException(409, "Workspace is already configured. Sign in.")
            try:
                db.execute(
                    "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)",
                    (user.user_id, user.email, tenant, json.dumps(user.roles), salt, digest),
                )
            except sqlite3.IntegrityError:
                raise HTTPException(409, "This account cannot be created.") from None
            if first:
                db.execute(
                    "INSERT INTO security_metadata VALUES ('legacy_owner', ?)", (user.user_id,)
                )
        return user

    def adopt_legacy(self, user, vectors):
        with self.catalog.connect() as db:
            row = db.execute("SELECT value FROM security_metadata WHERE key = 'legacy_owner'")
            owner = row.fetchone()
        if not owner or owner[0] != user.user_id:
            return
        # Idempotent: a partial first-run migration resumes on the first owner's login.
        for document in self.catalog.list():
            if not document.tenant_id:
                policy = AccessPolicy(tenant_id=user.tenant_id, owner_id=user.user_id)
                vectors.set_acl(document.document_id, policy.model_dump())
                self.catalog.set_acl(document, {**policy.model_dump(), "fingerprint": None})
                self.audit(user, "legacy_document_adopted", document_id=document.document_id)
        with self.catalog.connect() as db:
            db.execute("DELETE FROM security_metadata WHERE key = 'legacy_owner'")

    def audit(self, user, event, **data):
        audit_id = str(uuid4())
        with self.catalog.connect() as db:
            db.execute(
                "INSERT INTO audit_logs VALUES (?, ?, ?, ?, ?, ?)",
                (
                    audit_id,
                    user.tenant_id if user else "",
                    user.user_id if user else "",
                    event,
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps(data),
                ),
            )
        return audit_id

    def issue(self, user, response):
        now = int(time.time())
        session_id = str(uuid4())
        with self.catalog.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires <= ?", (now,))
            db.execute(
                "INSERT INTO sessions VALUES (?, ?, ?)",
                (
                    session_id,
                    user.user_id,
                    now + 3600,
                ),
            )
        token = jwt.encode(
            {
                "sub": user.user_id,
                "jti": session_id,
                "iat": now,
                "exp": now + 3600,
                "iss": "folio",
                "aud": "folio-api",
                "tenant_id": user.tenant_id,
                "roles": user.roles,
            },
            self.key,
            algorithm="HS256",
        )
        response.set_cookie(
            COOKIE,
            token,
            httponly=True,
            samesite="strict",
            max_age=3600,
            secure=self.settings.auth_cookie_secure,
            path="/",
        )
        self.audit(user, "authentication_success")
        return {"user": user}

    def authenticate(self, token):
        try:
            claims = jwt.decode(
                token,
                self.key,
                algorithms=["HS256"],
                issuer="folio",
                audience="folio-api",
                options={"require": ["exp", "iat", "sub", "jti", "iss", "aud"]},
            )
            with self.catalog.connect() as db:
                row = db.execute(
                    "SELECT u.id, u.email, u.tenant, u.roles FROM users u "
                    "JOIN sessions s ON s.user_id = u.id "
                    "WHERE u.id = ? AND s.id = ? AND s.expires > ?",
                    (claims["sub"], claims["jti"], time.time()),
                ).fetchone()
            if not row:
                raise ValueError()
            # Roles and tenant are reloaded on every request, never taken from the browser.
            return UserContext(
                user_id=row[0], email=row[1], tenant_id=row[2], roles=json.loads(row[3])
            ), claims["jti"]
        except (jwt.PyJWTError, ValueError, TypeError):
            raise HTTPException(401, "Your session has expired. Sign in again.") from None

    def throttle(self, email):
        key = hmac.new(self.key.encode(), email.lower().encode(), hashlib.sha256).hexdigest()
        now = int(time.time())
        with self.catalog.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM login_attempts WHERE since < ?", (now - 300,))
            row = db.execute("SELECT attempts FROM login_attempts WHERE key = ?", (key,)).fetchone()
            if row and row[0] >= 10:
                raise HTTPException(429, "Too many sign-in attempts. Try again in five minutes.")
            db.execute(
                "INSERT INTO login_attempts VALUES (?, 1, ?) "
                "ON CONFLICT(key) DO UPDATE SET attempts = attempts + 1",
                (key, now),
            )


def current_user(request: Request):
    header = request.headers.get("authorization")
    token = header[7:] if header and header.startswith("Bearer ") else None
    if header and token is None:
        raise HTTPException(401, "Use a Bearer token.")
    token = token or request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Sign in to access your workspace.")
    try:
        user, session = request.app.state.security.authenticate(token)
    except HTTPException:
        request.app.state.security.audit(None, "authentication_denied")
        raise
    request.state.session_id = session
    return user


def admin_user(request: Request, user=Depends(current_user)):
    if "admin" not in user.roles:
        request.app.state.security.audit(user, "permission_denied", action="admin")
        raise HTTPException(403, "An administrator is required for this action.")
    return user


router = APIRouter(prefix="/auth")


@router.get("/status")
def auth_status(request: Request):
    return {"setup_required": not request.app.state.security.configured()}


@router.post("/setup")
def setup(body: Setup, request: Request, response: Response):
    security = request.app.state.security
    user = security.add_user(body, body.tenant_id, ["admin"], first=True)
    security.adopt_legacy(user, request.app.state.vectors)
    security.audit(user, "workspace_created")
    return security.issue(user, response)


@router.post("/login")
def login(body: Credentials, request: Request, response: Response):
    security = request.app.state.security
    security.throttle(body.email)
    with security.catalog.connect() as db:
        row = db.execute(
            "SELECT id, email, tenant, roles, salt, password_hash FROM users WHERE email = ?",
            (body.email.lower(),),
        ).fetchone()
    digest = password_hash(body.password, row[4] if row else "00" * 16)
    if not row or not hmac.compare_digest(digest, row[5]):
        security.audit(None, "authentication_denied")
        raise HTTPException(401, "Email or password is incorrect.")
    user = UserContext(user_id=row[0], email=row[1], tenant_id=row[2], roles=json.loads(row[3]))
    security.adopt_legacy(user, request.app.state.vectors)
    return security.issue(user, response)


@router.get("/me")
def me(user=Depends(current_user)):
    return user


@router.post("/logout")
def logout(request: Request, response: Response, user=Depends(current_user)):
    with request.app.state.catalog.connect() as db:
        db.execute("DELETE FROM sessions WHERE id = ?", (request.state.session_id,))
    request.app.state.security.audit(user, "logout")
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/users")
def users(request: Request, user=Depends(admin_user)):
    with request.app.state.catalog.connect() as db:
        rows = db.execute(
            "SELECT id, email, roles FROM users WHERE tenant = ?", (user.tenant_id,)
        ).fetchall()
    return [
        UserContext(user_id=r[0], email=r[1], tenant_id=user.tenant_id, roles=json.loads(r[2]))
        for r in rows
    ]


@router.post("/users", status_code=201)
def create_user(body: NewUser, request: Request, user=Depends(admin_user)):
    created = request.app.state.security.add_user(body, user.tenant_id, body.roles)
    request.app.state.security.audit(user, "user_created", target_user_id=created.user_id)
    return created
