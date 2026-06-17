"""小程序后端 API 路由（挂到现有 FastAPI 服务，prefix=/api）。

约定：
- 所有响应为 JSON：{ "ok": true, ... } 或 {"ok": false, "error": "..."}（HTTP 仍尽量用 200/4xx）
- 鉴权：请求头 Authorization: Bearer <token>
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from . import auth, news_store, qa, douyin_cookie

log = logging.getLogger(__name__)

router = APIRouter()


# ---------------- 鉴权辅助 ----------------
def _current(authorization: str | None) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    token = authorization.split(" ", 1)[1].strip()
    account = auth.parse_token(token)
    if not account:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    u = auth.get_user(account)
    if not u:
        raise HTTPException(status_code=401, detail="用户不存在")
    return u


def _require_admin(authorization: str | None) -> dict:
    u = _current(authorization)
    if not u.get("is_admin"):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return u


def _require_super(authorization: str | None) -> dict:
    u = _current(authorization)
    if not u.get("is_super"):
        raise HTTPException(status_code=403, detail="需要超级管理员权限")
    return u


def _target_is_admin(target: dict) -> bool:
    return target.get("role") in ("admin", "super_admin")


# ---------------- 健康检查 ----------------
@router.get("/ping")
def ping():
    return {"ok": True, "service": "weixin_miniprogram"}


# ---------------- 认证 ----------------
class RegisterIn(BaseModel):
    account: str
    real_name: str
    password: str


class LoginIn(BaseModel):
    account: str
    password: str


@router.post("/auth/register")
def api_register(body: RegisterIn):
    try:
        user = auth.register(body.account, body.real_name, body.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "token": auth.make_token(user["account"]), "user": user}


@router.post("/auth/login")
def api_login(body: LoginIn):
    try:
        user = auth.login(body.account, body.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "token": auth.make_token(user["account"]), "user": user}


@router.get("/auth/me")
def api_me(authorization: str | None = Header(default=None)):
    return {"ok": True, "user": _current(authorization)}


class ChangePwdIn(BaseModel):
    old_password: str
    new_password: str


@router.post("/auth/change_password")
def api_change_pwd(body: ChangePwdIn, authorization: str | None = Header(default=None)):
    u = _current(authorization)
    try:
        user = auth.change_own_password(u["account"], body.old_password, body.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "user": user}


# ---------------- 新闻 ----------------
@router.get("/news/week")
def api_news_week(days: int = 7, kind: str | None = None):
    days = max(1, min(days, 14))
    return {"ok": True, **news_store.week(days=days, kind=kind)}


@router.get("/news/item")
def api_news_item(id: str):
    it = news_store.detail(id)
    if not it:
        raise HTTPException(status_code=404, detail="未找到该条目（可能已超出保留期）")
    return {"ok": True, "item": it}


# ---------------- 问答 ----------------
class AskIn(BaseModel):
    question: str


@router.post("/qa/ask")
def api_ask(body: AskIn, authorization: str | None = Header(default=None)):
    _current(authorization)  # 需登录
    try:
        answer = qa.ask(body.question)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa
        log.exception("qa failed: %s", e)
        raise HTTPException(status_code=500, detail=f"回答失败：{e}")
    return {"ok": True, "answer": answer}


# ---------------- 管理员：用户管理 ----------------
@router.get("/admin/users")
def api_admin_users(authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    return {"ok": True, "users": auth.admin_list_users()}


class AdminUpdateIn(BaseModel):
    account: str
    real_name: str | None = None
    role: str | None = None
    new_password: str | None = None


@router.post("/admin/users/update")
def api_admin_update(body: AdminUpdateIn, authorization: str | None = Header(default=None)):
    actor = _require_admin(authorization)
    target = auth.get_user(body.account)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    is_self = target["account"].lower() == actor["account"].lower()

    # 改角色：仅超级管理员可操作
    if body.role is not None and not actor["is_super"]:
        raise HTTPException(status_code=403, detail="只有超级管理员可以修改角色")

    # 非超管：不能修改其他管理员（含改密/改名），只能管普通用户或自己
    if not actor["is_super"] and _target_is_admin(target) and not is_self:
        raise HTTPException(status_code=403, detail="无权修改其他管理员")

    try:
        user = auth.update_user(
            body.account, real_name=body.real_name,
            role=body.role, new_password=body.new_password,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "user": user}


class AdminDeleteIn(BaseModel):
    account: str


@router.post("/admin/users/delete")
def api_admin_delete(body: AdminDeleteIn, authorization: str | None = Header(default=None)):
    actor = _require_admin(authorization)
    target = auth.get_user(body.account)
    if not target:
        return {"ok": True}
    if target["account"].lower() == actor["account"].lower():
        raise HTTPException(status_code=400, detail="不能删除自己")
    # 非超管不能删除管理员；超管可删除任意（自己除外）
    if not actor["is_super"] and _target_is_admin(target):
        raise HTTPException(status_code=403, detail="无权删除其他管理员")
    auth.delete_user(body.account)
    return {"ok": True}


# ---------------- 超级管理员：抖音 Cookie ----------------
@router.get("/admin/douyin/status")
def api_dy_status(authorization: str | None = Header(default=None)):
    _require_super(authorization)
    return {"ok": True, **douyin_cookie.status()}


class DyCookieIn(BaseModel):
    cookie: str


@router.post("/admin/douyin/cookie")
def api_dy_cookie(body: DyCookieIn, authorization: str | None = Header(default=None)):
    _require_super(authorization)
    try:
        result = douyin_cookie.update_cookie(body.cookie)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa
        log.exception("update douyin cookie failed: %s", e)
        raise HTTPException(status_code=500, detail=f"更新失败：{e}")
    return {"ok": True, **result}
