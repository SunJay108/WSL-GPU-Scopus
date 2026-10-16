"""
Sign-up / login endpoints of the search UI (accounts in auth.py, session in
an HttpOnly cookie).
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import audit, auth
from .config import ALLOW_SIGNUP, SESSION_DAYS


class Credentials(BaseModel):
    username: str
    password: str


class PasswordChange(BaseModel):
    current: str
    new: str


def client_ip(request: Request):
    return request.client.host if request.client else None


class WebAuth:
    """
    Adds /api/auth/{state,signup,login,logout,password} to an app.
    `service` is recorded in the activity log.
    """

    def __init__(self, cookie: str, service: str):
        self.cookie = cookie
        self.service = service
        self.router = APIRouter()
        self._add_routes()

    def install(self, app):
        app.include_router(self.router)

        @app.exception_handler(auth.AuthError)
        async def _auth_error(request, exc):
            return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    # ---------- dependencies ----------

    def optional_user(self, request: Request):
        return auth.user_for_session(request.cookies.get(self.cookie))

    def current_user(self, request: Request) -> dict:
        user = self.optional_user(request)
        if user is None:
            raise HTTPException(401, "Please sign in.")
        return user

    def admin_user(self, request: Request) -> dict:
        user = self.current_user(request)
        if user["role"] != "admin":
            raise HTTPException(403, "Administrators only.")
        return user

    # ---------- helpers ----------

    def _log(self, action, user=None, request=None, detail=None, username=None):
        audit.log(action, user, detail={"service": self.service, **(detail or {})},
                  ip=client_ip(request) if request else None, username=username)

    def _signed_in(self, user, request: Request) -> JSONResponse:
        token = auth.create_session(user["id"], client_ip(request), request.headers.get("user-agent"))
        response = JSONResponse({"user": auth.public(user)})
        response.set_cookie(self.cookie, token, max_age=SESSION_DAYS * 86400,
                            httponly=True, samesite="lax", path="/")
        return response

    # ---------- routes ----------

    def _add_routes(self):
        router = self.router

        @router.get("/api/auth/state")
        def auth_state(request: Request):
            user = self.optional_user(request)
            first = auth.user_count() == 0
            return {
                "user": auth.public(user) if user else None,
                "allow_signup": ALLOW_SIGNUP or first,
                "first_user": first,
            }

        @router.post("/api/auth/signup")
        def signup(body: Credentials, request: Request):
            if not ALLOW_SIGNUP and auth.user_count() > 0:
                raise HTTPException(403, "Sign-up is disabled. Ask an administrator for an account.")
            user = auth.create_user(body.username, body.password)
            self._log("signup", user, request, {"role": user["role"]})
            self._log("login", user, request)
            return self._signed_in(user, request)

        @router.post("/api/auth/login")
        def login(body: Credentials, request: Request):
            try:
                user = auth.authenticate(body.username, body.password, ip=client_ip(request))
            except auth.AuthError as exc:
                if exc.status in (401, 403):
                    self._log("login_failed", request=request, detail={"reason": str(exc)},
                              username=body.username.strip()[:64])
                raise
            self._log("login", user, request)
            return self._signed_in(user, request)

        @router.post("/api/auth/logout")
        def logout(request: Request):
            token = request.cookies.get(self.cookie)
            user = auth.user_for_session(token)
            auth.delete_session(token)
            if user:
                self._log("logout", user, request)
            response = JSONResponse({"ok": True})
            response.delete_cookie(self.cookie, path="/")
            return response

        @router.post("/api/auth/password")
        def change_password(body: PasswordChange, request: Request, user: dict = Depends(self.current_user)):
            auth.change_password(user["id"], body.current, body.new)
            self._log("password_change", user, request)
            return {"ok": True}
