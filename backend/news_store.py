"""读取 weixin_auto_message 已生成的速递缓存，聚合「近一周」新闻给小程序。

不重新抓取、不推送，纯复用 data/cache/*.json。
每个缓存含 spacenews[] / opml[] / douyin[]，本模块把它们规整成统一条目。
"""
from __future__ import annotations

import glob
import hashlib
import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from .paths import WAM_CACHE_DIR, ensure_wam_importable

# 跨项目模块（gzh_store / social_store / img_proxy）在模块加载时**一次性**导入，
# 避免第一个请求在请求线程里现 import（实测曾导致冷启动额外 +3.6s）。
ensure_wam_importable()
try:
    from src import gzh_store as _gzh_store  # type: ignore
except Exception:
    _gzh_store = None
try:
    from src import social_store as _social_store  # type: ignore
except Exception:
    _social_store = None
try:
    from src import img_proxy as _img_proxy  # type: ignore
except Exception:
    _img_proxy = None

CST = timezone(timedelta(hours=8))
_CN_DT_RE = re.compile(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2}):(\d{2})(?::(\d{2}))?")

# 统一构建近一个月（含冗余）的全量数据集，按需在 week()/detail() 里按天过滤；
# 这样缓存与请求的 days 解耦，避免不同 days 互相挤掉对方触发重建。
_WINDOW_DAYS = 31

# 单一缓存 + stale-while-revalidate：缓存过期时**先返回旧数据**、后台线程重建，
# 请求永不阻塞在重建上（首次无缓存时才同步构建一次）。
_CACHE: dict = {"ts": 0.0, "items": [], "index": {}}
_CACHE_TTL = 300  # 秒
_BUILD_LOCK = threading.Lock()
_REFRESHING = {"on": False}


def _parse_dt(s: str) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    try:
        d = parsedate_to_datetime(s)
        if d is not None:
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    m = _CN_DT_RE.search(s)
    if m:
        y, mo, da, h, mi, se = m.groups()
        try:
            return datetime(int(y), int(mo), int(da), int(h), int(mi), int(se or 0), tzinfo=CST)
        except Exception:
            return None
    return None


def _to_beijing(s: str) -> str:
    d = _parse_dt(s)
    if not d:
        return s or ""
    return d.astimezone(CST).strftime("%Y-%m-%d %H:%M")


def _ts(s: str) -> int:
    d = _parse_dt(s)
    return int(d.timestamp()) if d else 0


def _mk_id(kind: str, link: str, title: str) -> str:
    raw = f"{kind}|{link}|{title}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _clean_text(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "")
    return re.sub(r"\s+\n", "\n", s).strip()


def _short(s: str, n: int = 80) -> str:
    s = _clean_text(s)
    return s[:n] + ("…" if len(s) > n else "")


def _norm_intl(a: dict) -> dict:
    title = a.get("title_zh") or a.get("title") or ""
    body = a.get("body_zh") or a.get("summary") or ""
    tags = a.get("tags") or []
    link = a.get("link") or a.get("original_link") or ""
    pub = a.get("published") or ""
    return {
        "id": _mk_id("intl", link, title),
        "kind": "intl",
        "title": title,
        "title_orig": a.get("title") or "",
        "summary": _short(body, 90),
        "body": body,
        "source": a.get("source") or "SpaceNews",
        "published": _to_beijing(pub),
        "published_ts": _ts(pub),
        "tags": (tags or ["国际新闻"]),
        "main_tag": (tags[0] if tags else "国际新闻"),
        "image": a.get("image_url") or "",
        "link": link,
    }


def _norm_gzh(a: dict) -> dict:
    title = a.get("title") or ""
    body = a.get("description") or ""
    link = a.get("link") or ""
    pub = a.get("published") or ""
    return {
        "id": _mk_id("gzh", link, title),
        "kind": "gzh",
        "title": title,
        "title_orig": title,
        "summary": _short(body, 90),
        "body": body,
        "source": a.get("source") or "公众号",
        "published": _to_beijing(pub),
        "published_ts": _ts(pub),
        "tags": ["公众号精选"],
        "main_tag": "公众号精选",
        "image": a.get("image_url") or "",
        "link": link,
    }


def _norm_dy(a: dict) -> dict:
    title = a.get("title") or f"抖音·{a.get('source','')}"
    body = a.get("desc") or a.get("share_text") or ""
    link = a.get("link") or a.get("share_url") or ""
    pub = a.get("published") or ""
    return {
        "id": _mk_id("douyin", link, title),
        "kind": "douyin",
        "title": title,
        "title_orig": title,
        "summary": _short(body, 90),
        "body": body,
        "source": a.get("source") or "抖音",
        "published": _to_beijing(pub),
        "published_ts": _ts(pub),
        "tags": ["航天视频"],
        "main_tag": "航天视频",
        "image": a.get("image_url") or "",
        "link": link,
        "share_text": a.get("share_text") or "",
    }


def _proxy_img(url: str) -> str:
    if not url or _img_proxy is None:
        return url or ""
    try:
        return _img_proxy.proxify(url)
    except Exception:
        return url


def _norm_social(a: dict) -> dict:
    """政要社媒条目（X / Truth Social），含原文/译文/解读供详情页结构化展示。"""
    author = a.get("author_name") or a.get("author") or ""
    channel = a.get("channel") or a.get("platform") or ""
    title = a.get("title") or f"{author}最新动态"
    translation = a.get("translation") or ""
    link = a.get("url") or ""
    pub = a.get("published") or ""
    return {
        "id": _mk_id("social", a.get("post_id") or link, title),
        "kind": "social",
        "title": title,
        "title_orig": title,
        "summary": _short(translation or a.get("original") or "", 90),
        "body": translation,
        "source": f"{author}·{channel}".strip("·"),
        "published": _to_beijing(pub),
        "published_ts": _ts(pub),
        "tags": ["政要社媒", author] if author else ["政要社媒"],
        "main_tag": "政要社媒",
        "image": _proxy_img(a.get("image") or ""),
        "link": link,
        # 详情页专用
        "author_name": author,
        "channel": channel,
        "original": a.get("original") or "",
        "translation": translation,
        "analysis": a.get("analysis") or "",
    }


def _build(days: int) -> tuple[list[dict], dict]:
    cutoff = time.time() - days * 86400
    files = sorted(glob.glob(str(WAM_CACHE_DIR / "*.json")))
    seen: set[str] = set()
    items: list[dict] = []
    for f in files:
        try:
            d = json.loads(open(f, encoding="utf-8").read())
        except Exception:
            continue
        gen = _ts(d.get("generated_at") or "") or 0
        # 文件名里的日期兜底
        if gen == 0:
            m = re.search(r"(\d{4})-(\d{2})-(\d{2})", f)
            if m:
                try:
                    gen = int(datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=CST).timestamp())
                except Exception:
                    gen = 0
        if gen and gen < cutoff:
            continue
        for a in d.get("spacenews") or []:
            it = _norm_intl(a)
            if it["link"] and it["id"] not in seen:
                seen.add(it["id"])
                items.append(it)
        for a in d.get("opml") or []:
            it = _norm_gzh(a)
            if it["link"] and it["id"] not in seen:
                seen.add(it["id"])
                items.append(it)
        for a in d.get("douyin") or []:
            it = _norm_dy(a)
            if it["link"] and it["id"] not in seen:
                seen.add(it["id"])
                items.append(it)

    # 合并独立公众号库：保证每条抓到过的公众号更新都可见（不受推送去重/批次影响）
    if _gzh_store is not None:
        try:
            for a in _gzh_store.load_recent(days):
                it = _norm_gzh(a)
                if it["link"] and it["id"] not in seen:
                    seen.add(it["id"])
                    items.append(it)
        except Exception:
            pass

    # 合并政要社媒库（X / Truth Social，已 LLM 富化）
    if _social_store is not None:
        try:
            for a in _social_store.load_recent(days):
                it = _norm_social(a)
                if it["id"] not in seen:
                    seen.add(it["id"])
                    items.append(it)
        except Exception:
            pass

    items.sort(key=lambda x: x["published_ts"], reverse=True)
    index = {it["id"]: it for it in items}
    return items, index


def _do_build_and_store() -> tuple[list[dict], dict]:
    items, index = _build(_WINDOW_DAYS)
    _CACHE.update(ts=time.time(), items=items, index=index)
    return items, index


def _ensure() -> tuple[list[dict], dict]:
    """返回全量数据集（近 _WINDOW_DAYS 天），days-agnostic。

    - 缓存新鲜：直接返回。
    - 缓存陈旧但存在：立即返回旧数据，后台线程重建（stale-while-revalidate）。
    - 完全无缓存（首次）：同步构建一次（加锁防并发重复构建）。
    """
    now = time.time()
    if (now - _CACHE["ts"] < _CACHE_TTL) and _CACHE["items"]:
        return _CACHE["items"], _CACHE["index"]

    if _CACHE["items"]:
        # 有旧数据 → 不阻塞，后台刷新
        if not _REFRESHING["on"]:
            _REFRESHING["on"] = True

            def _bg():
                try:
                    with _BUILD_LOCK:
                        _do_build_and_store()
                except Exception:
                    pass
                finally:
                    _REFRESHING["on"] = False

            threading.Thread(target=_bg, daemon=True).start()
        return _CACHE["items"], _CACHE["index"]

    # 首次：同步构建（双检锁，避免并发重复构建）
    with _BUILD_LOCK:
        if _CACHE["items"]:
            return _CACHE["items"], _CACHE["index"]
        return _do_build_and_store()


def warm() -> None:
    """服务启动时后台预热缓存，使第一个用户请求即命中。"""
    try:
        with _BUILD_LOCK:
            _do_build_and_store()
    except Exception:
        pass


def _card(it: dict) -> dict:
    """列表卡片用的精简字段（不含大正文）。"""
    return {k: it[k] for k in (
        "id", "kind", "title", "summary", "source", "published",
        "tags", "main_tag", "image", "link",
    )}


def week(days: int = 30, kind: str | None = None) -> dict:
    items, _ = _ensure()
    cutoff = time.time() - days * 86400
    # published_ts==0 表示日期无法解析，保留以免误删
    items = [it for it in items if (it["published_ts"] == 0 or it["published_ts"] >= cutoff)]
    if kind:
        items = [it for it in items if it["kind"] == kind]
    cards = [_card(it) for it in items]
    return {
        "days": days,
        "count": len(cards),
        "items": cards,
        "kinds": {
            "intl": sum(1 for c in cards if c["kind"] == "intl"),
            "gzh": sum(1 for c in cards if c["kind"] == "gzh"),
            "douyin": sum(1 for c in cards if c["kind"] == "douyin"),
            "social": sum(1 for c in cards if c["kind"] == "social"),
        },
    }


def detail(item_id: str, days: int = 31) -> dict | None:
    _, index = _ensure()
    it = index.get(item_id)
    if it is None:
        # 缓存未命中时强制重建一次（加锁）
        with _BUILD_LOCK:
            _, index = _do_build_and_store()
        it = index.get(item_id)
    return it


def latest_qa_context(limit: int = 30) -> list[dict]:
    """给问答用的背景材料：取最近的国际+公众号条目。"""
    items, _ = _ensure()
    ctx = [it for it in items if it["kind"] in ("intl", "gzh")][:limit]
    return [
        {
            "source": it["source"],
            "title": it["title"],
            "link": it["link"],
            "published": it["published"],
            "summary": it["summary"],
        }
        for it in ctx
    ]
