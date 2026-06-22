"""读取 weixin_auto_message 已生成的速递缓存，聚合「近一周」新闻给小程序。

不重新抓取、不推送，纯复用 data/cache/*.json。
每个缓存含 spacenews[] / opml[] / douyin[]，本模块把它们规整成统一条目。
"""
from __future__ import annotations

import glob
import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from .paths import WAM_CACHE_DIR, ensure_wam_importable

CST = timezone(timedelta(hours=8))
_CN_DT_RE = re.compile(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2}):(\d{2})(?::(\d{2}))?")

# 简易缓存：避免每次请求都重新解析所有文件
_CACHE: dict = {"ts": 0.0, "days": 0, "items": [], "index": {}}
_CACHE_TTL = 120  # 秒


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
    if not url:
        return ""
    try:
        ensure_wam_importable()
        from src import img_proxy  # type: ignore
        return img_proxy.proxify(url)
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
    try:
        ensure_wam_importable()
        from src import gzh_store  # type: ignore
        for a in gzh_store.load_recent(days):
            it = _norm_gzh(a)
            if it["link"] and it["id"] not in seen:
                seen.add(it["id"])
                items.append(it)
    except Exception:
        pass

    # 合并政要社媒库（X / Truth Social，已 LLM 富化）
    try:
        ensure_wam_importable()
        from src import social_store  # type: ignore
        for a in social_store.load_recent(days):
            it = _norm_social(a)
            if it["id"] not in seen:
                seen.add(it["id"])
                items.append(it)
    except Exception:
        pass

    items.sort(key=lambda x: x["published_ts"], reverse=True)
    index = {it["id"]: it for it in items}
    return items, index


def _ensure(days: int) -> tuple[list[dict], dict]:
    now = time.time()
    if (now - _CACHE["ts"] < _CACHE_TTL) and _CACHE["days"] == days and _CACHE["items"]:
        return _CACHE["items"], _CACHE["index"]
    items, index = _build(days)
    _CACHE.update(ts=now, days=days, items=items, index=index)
    return items, index


def _card(it: dict) -> dict:
    """列表卡片用的精简字段（不含大正文）。"""
    return {k: it[k] for k in (
        "id", "kind", "title", "summary", "source", "published",
        "tags", "main_tag", "image", "link",
    )}


def week(days: int = 7, kind: str | None = None) -> dict:
    items, _ = _ensure(days)
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


def detail(item_id: str, days: int = 9) -> dict | None:
    _, index = _ensure(days)
    it = index.get(item_id)
    if it is None:
        # 缓存未命中时强制重建一次
        items, index = _build(days)
        _CACHE.update(ts=time.time(), days=days, items=items, index=index)
        it = index.get(item_id)
    return it


def latest_qa_context(limit: int = 30) -> list[dict]:
    """给问答用的背景材料：取最近的国际+公众号条目。"""
    ensure_wam_importable()
    items, _ = _ensure(7)
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
