"""小程序后端 API 路由（挂到现有 FastAPI 服务，prefix=/api）。

约定：
- 所有响应为 JSON：{ "ok": true, ... } 或 {"ok": false, "error": "..."}（HTTP 仍尽量用 200/4xx）
- 鉴权：请求头 Authorization: Bearer <token>
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from . import auth, news_store, qa, douyin_cookie, topic_intel, search_store, topic_requests
from .paths import DATA_DIR, WAM_DIR

log = logging.getLogger(__name__)

router = APIRouter()
topic_requests.recover_interrupted()

# 小程序「真实内容 / 计算器」总开关：改它无需重新提交小程序版本。
# 优先读 data/gate.json（改文件即时生效、无需重启）；文件缺失时回退环境变量 MP_SHOW_REAL；
# 默认 False（显示计算器）。文件内容示例：{"real": true}
_GATE_FILE = DATA_DIR / "gate.json"


def _gate_real() -> bool:
    try:
        if _GATE_FILE.exists():
            data = json.loads(_GATE_FILE.read_text(encoding="utf-8"))
            return bool(data.get("real"))
    except Exception:
        log.warning("读取 gate.json 失败，回退环境变量/默认值", exc_info=True)
    return os.environ.get("MP_SHOW_REAL", "").strip().lower() in ("1", "true", "yes", "on")


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


# ---------------- 前端展示总开关 ----------------
@router.get("/gate")
def gate():
    """小程序据此决定展示真实内容(real=true)还是计算器(real=false)。"""
    return {"ok": True, "real": _gate_real()}


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
def api_news_week(days: int = 14, kind: str | None = None, offset: int = 0, limit: int = 0):
    days = max(1, min(days, 15))
    offset = max(0, offset)
    # limit<=0 表示不分页（兼容旧逻辑）；分页时限制单页上限，防止超大响应
    limit = min(limit, 100) if limit and limit > 0 else 0
    return {"ok": True, **news_store.week(days=days, kind=kind, offset=offset, limit=limit)}


@router.get("/news/item")
def api_news_item(id: str):
    it = news_store.detail(id)
    if not it:
        # 近两周窗口之外的搜索结果，回退到全量搜索索引里找（覆盖历史归档）
        it = search_store.get_item(id)
    if not it:
        raise HTTPException(status_code=404, detail="未找到该条目（可能已超出保留期）")
    return {"ok": True, "item": it}


@router.get("/news/search")
def api_news_search(q: str = "", kind: str | None = None, sort: str = "time",
                     scope: str = "all", offset: int = 0, limit: int = 20):
    """全量语义搜索（embedding 跨中英）+ 双语关键词补强；覆盖归档以来全部历史。

    scope=title 只匹配标题；scope=all（默认）标题+正文等。sort=score 按相关度。
    """
    q = (q or "").strip()
    offset = max(0, offset)
    limit = min(limit, 50) if limit and limit > 0 else 20
    sort = sort if sort in ("time", "score") else "time"
    scope = scope if scope in ("all", "title") else "all"
    if not q:
        return {"ok": True, "q": "", "sort": sort, "scope": scope, "total": 0, "count": 0,
                "offset": offset, "limit": limit, "has_more": False, "items": []}
    return {"ok": True, **search_store.search(q, kind=kind, sort=sort, scope=scope,
                                               offset=offset, limit=limit)}


# ---------------- 每周概览 ----------------
_WEEK_ID_RE = re.compile(r"^\d{4}-W\d{2}$")
_HIGHLIGHTS_DIR = WAM_DIR / "data" / "highlights"


@router.get("/weekly/get")
def api_weekly_get(week: str = ""):
    """返回群发任务对应的冻结周快照，而不是随时间滚动的新闻窗口。"""
    week = (week or "").strip()
    if week and not _WEEK_ID_RE.fullmatch(week):
        raise HTTPException(status_code=400, detail="week 格式应为 YYYY-Www")
    if not week:
        candidates = sorted(
            path.parent.name
            for path in _HIGHLIGHTS_DIR.glob("*/manifest.json")
            if _WEEK_ID_RE.fullmatch(path.parent.name)
        )
        if not candidates:
            raise HTTPException(status_code=404, detail="暂无每周概览")
        week = candidates[-1]

    path = _HIGHLIGHTS_DIR / week / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="未找到该周概览")
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("读取周概览失败 week=%s: %s", week, exc)
        raise HTTPException(status_code=500, detail="周概览数据暂不可用")

    overview = manifest.get("weekly_overview") or {}
    item_ids = overview.get("item_ids") or []
    by_id = {str(item.get("id")): item for item in manifest.get("items") or [] if item.get("id")}
    items = [by_id[item_id] for item_id in item_ids if item_id in by_id]
    return {
        "ok": True,
        "week_id": week,
        "title": manifest.get("title", ""),
        "period": manifest.get("period", ""),
        "summary": manifest.get("summary", ""),
        "overview_text": overview.get("text", ""),
        "item_count": manifest.get("item_count", len(manifest.get("items") or [])),
        "items": items,
    }


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


# ---------------- 专题情报 ----------------
def _run_topic_request(request_id: str) -> None:
    try:
        request = topic_requests.get(request_id)
        if not request or request.get("status") not in ("queued", "failed"):
            return
        topic_requests.update(request_id, status="running", error="")
        request = topic_requests.get(request_id) or request
        topic = topic_intel.build_requested_topic(request)
        topic_requests.update(request_id, status="done", topic_id=topic["id"])
    except Exception as exc:
        log.exception("topic request failed id=%s", request_id)
        try:
            topic_requests.update(request_id, status="failed", error=str(exc))
        except ValueError:
            pass


def _start_topic_request(request_id: str) -> None:
    threading.Thread(
        target=_run_topic_request,
        args=(request_id,),
        name=f"topic-request-{request_id}",
        daemon=True,
    ).start()


def _notify_topic_approval(request: dict) -> None:
    try:
        topic_intel.notify_super_admin_request(request)
    except Exception:
        log.exception("notify super admin failed for topic request id=%s", request.get("id"))


def _start_topic_approval_notice(request: dict) -> None:
    threading.Thread(
        target=_notify_topic_approval,
        args=(request,),
        name=f"topic-approval-notice-{request.get('id', '')}",
        daemon=True,
    ).start()


class TopicApplyIn(BaseModel):
    title: str
    intro: str = ""
    keywords: list[str] = Field(default_factory=list)


@router.post("/topic/apply")
def api_topic_apply(body: TopicApplyIn, authorization: str | None = Header(default=None)):
    applicant = _require_admin(authorization)
    title = body.title.strip()
    if len(title) < 2 or len(title) > 60:
        raise HTTPException(status_code=400, detail="专题名称应为 2-60 个字符")
    keywords = list(dict.fromkeys(
        value.strip() for value in (body.keywords or [title]) if value.strip()
    ))
    if not keywords:
        keywords = [title]
    estimate = topic_intel.estimate_request(keywords)
    try:
        request = topic_requests.create(
            applicant=applicant["account"],
            title=title,
            intro=body.intro,
            keywords=keywords,
            estimate=estimate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if request["status"] == "queued":
        _start_topic_request(request["id"])
    elif request["status"] == "pending":
        _start_topic_approval_notice(request)
    return {"ok": True, "request": request}


@router.get("/topic/requests/mine")
def api_topic_requests_mine(authorization: str | None = Header(default=None)):
    applicant = _require_admin(authorization)
    return {"ok": True, "requests": topic_requests.list_for(applicant["account"])}


class TopicDecisionIn(BaseModel):
    id: str
    action: str
    seed_urls: list[str] = Field(default_factory=list)
    note: str = ""


@router.get("/admin/topic/requests")
def api_admin_topic_requests(authorization: str | None = Header(default=None)):
    _require_super(authorization)
    return {"ok": True, "requests": topic_requests.list_all()}


@router.post("/admin/topic/requests/decide")
def api_admin_topic_decide(
    body: TopicDecisionIn,
    authorization: str | None = Header(default=None),
):
    actor = _require_super(authorization)
    request = topic_requests.get(body.id)
    if not request:
        raise HTTPException(status_code=404, detail="专题申请不存在")
    action = body.action.strip().lower()
    if action == "reject":
        if request.get("status") not in ("pending", "failed"):
            raise HTTPException(status_code=400, detail="当前状态不能拒绝")
        updated = topic_requests.update(
            body.id,
            status="rejected",
            decided_by=actor["account"],
            decision_note=body.note.strip(),
        )
        return {"ok": True, "request": updated}
    if action not in ("approve", "retry"):
        raise HTTPException(status_code=400, detail="action 只能是 approve/reject/retry")
    if request.get("status") not in ("pending", "failed"):
        raise HTTPException(status_code=400, detail="当前状态不能执行")
    seed_urls = list(dict.fromkeys(
        url.strip() for url in body.seed_urls if url.strip().startswith(("http://", "https://"))
    ))
    effective_urls = seed_urls or request.get("seed_urls") or []
    estimate = request.get("estimate") or {}
    if request.get("cost_tier") == "high":
        needed = max(
            1,
            int(estimate.get("minimum_complete") or 12)
            - int(estimate.get("complete_count") or 0),
        )
        if len(effective_urls) < needed:
            raise HTTPException(
                status_code=400,
                detail=f"现有完整资料不足，请至少补充 {needed} 个种子网址",
            )
    updated = topic_requests.update(
        body.id,
        status="queued",
        seed_urls=effective_urls,
        decided_by=actor["account"],
        decision_note=body.note.strip(),
        error="",
    )
    _start_topic_request(body.id)
    return {"ok": True, "request": updated}


@router.get("/topic/list")
def api_topic_list():
    return {"ok": True, "topics": topic_intel.list_topics()}


@router.get("/topic/get")
def api_topic_get(id: str):
    t = topic_intel.get_topic(id)
    if not t:
        raise HTTPException(status_code=404, detail="专题不存在")
    return {"ok": True, "topic": t}


@router.get("/topic/item")
def api_topic_item(topic: str, id: str):
    it = topic_intel.get_item(topic, id)
    if not it:
        raise HTTPException(status_code=404, detail="条目不存在")
    # 适配小程序详情页（pages/detail）所需字段，正文在小程序内浏览
    item = {
        "id": it.get("id"),
        "kind": "topic",
        "title": it.get("title", ""),
        "tags": it.get("tags") or [],
        "main_tag": it.get("aspect") or "专题",
        "source": it.get("source", ""),
        "published": it.get("published", ""),
        "image": it.get("image", ""),
        "images": it.get("images") or [],
        "body": it.get("body_zh") or it.get("summary", ""),
        "link": it.get("url", ""),
    }
    return {"ok": True, "item": item}


class TopicRefreshIn(BaseModel):
    id: str = "space-tug"


@router.post("/topic/refresh")
def api_topic_refresh(body: TopicRefreshIn, authorization: str | None = Header(default=None)):
    _require_super(authorization)
    try:
        t = topic_intel.refresh(body.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "topic": t}


class TopicPushIn(BaseModel):
    id: str = "space-tug"
    scope: str  # admin | all


@router.post("/topic/push")
def api_topic_push(body: TopicPushIn, authorization: str | None = Header(default=None)):
    _require_super(authorization)
    try:
        result = topic_intel.push(body.id, body.scope)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa
        log.exception("topic push failed: %s", e)
        raise HTTPException(status_code=500, detail=f"推送失败：{e}")
    return result


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
