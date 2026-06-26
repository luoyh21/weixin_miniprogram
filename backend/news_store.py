"""读取 weixin_auto_message 已生成的速递缓存，聚合「近两周」新闻给小程序。

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
    from src import techport_store as _techport_store  # type: ignore
except Exception:
    _techport_store = None
try:
    from src import launch_store as _launch_store  # type: ignore
except Exception:
    _launch_store = None
try:
    from src import debris_store as _debris_store  # type: ignore
except Exception:
    _debris_store = None
try:
    from src import img_proxy as _img_proxy  # type: ignore
except Exception:
    _img_proxy = None

CST = timezone(timedelta(hours=8))
_CN_DT_RE = re.compile(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2}):(\d{2})(?::(\d{2}))?")

# 统一构建近两周（含 1 天冗余）的全量数据集，按需在 week()/detail() 里按天过滤；
# 这样缓存与请求的 days 解耦，避免不同 days 互相挤掉对方触发重建。
_WINDOW_DAYS = 15

# 单一缓存 + stale-while-revalidate：缓存过期时**先返回旧数据**、后台线程重建，
# 请求永不阻塞在重建上（首次无缓存时才同步构建一次）。
_CACHE: dict = {"ts": 0.0, "items": [], "index": {}}
_CACHE_TTL = 300  # 秒
_BUILD_LOCK = threading.Lock()


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
        "image": _proxy_img(a.get("image_url") or ""),
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
        "image": _proxy_img(a.get("image_url") or ""),
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
        "image": _proxy_img(a.get("image_url") or ""),
        "link": link,
        "share_text": a.get("share_text") or "",
    }


def _phone_direct(url: str) -> bool:
    """该源服务器取不到、但**国内手机能直连**（truthsocial CDN 实测如此）→ 留原址直发手机。"""
    h = url.split("://", 1)[-1].split("/", 1)[0].lower()
    return h == "truthsocial.com" or h.endswith(".truthsocial.com")


def _proxy_img(url: str) -> str:
    # 列表缩略图：境外图床（i0.wp.com / nasa.gov 等）国内手机直连不到，但**本服务器可达**，
    # 故统一走本机 /img 代理——首次下载落盘、之后静态秒回（冷取高峰由 _prewarm_images 预热消解，
    # 不会再像早期那样每张冷图占线程把服务拖垮）。
    # 例外：已是本机地址(/relay-img、/img) 原样返回；truthsocial 服务器取不到、手机能直连 → 留原址。
    if not url:
        return ""
    if not url.startswith("http"):
        return url
    if _img_proxy is not None and url.startswith(_img_proxy.public_base()):
        return url
    if _phone_direct(url):
        return url
    if _img_proxy is not None:
        return _img_proxy.proxify(url)
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


def _norm_techport(a: dict) -> dict:
    """技术港（NASA TechPort 每日更新项目，标题/摘要已译中文）。"""
    title = a.get("title") or ""
    body = a.get("summary") or ""
    link = a.get("link") or ""
    pub = a.get("published") or ""
    extra = []
    if a.get("status"):
        extra.append(f"项目状态：{a['status']}")
    if a.get("title_en"):
        extra.append(f"原题：{a['title_en']}")
    full = (body + ("\n\n" + " · ".join(extra) if extra else "")).strip()
    return {
        "id": _mk_id("techport", link, title),
        "kind": "techport",
        "title": title,
        "title_orig": a.get("title_en") or title,
        "summary": _short(body, 90),
        "body": full,
        "source": "NASA TechPort",
        "published": _to_beijing(pub),
        "published_ts": _ts(pub),
        "tags": ["技术港"],
        "main_tag": "技术港",
        "image": "",
        "link": link,
    }


def _norm_launch(a: dict) -> dict:
    """每日发射（The Space Devs LL2 当日发射，火箭/任务名已译中文）。"""
    title = a.get("title") or a.get("name_en") or ""
    link = a.get("link") or ""
    pub = a.get("published") or ""
    parts = []
    if a.get("provider"):
        parts.append(f"发射提供方：{a['provider']}")
    loc = "，".join([p for p in (a.get("pad"), a.get("location")) if p])
    if loc:
        parts.append(f"发射场：{loc}")
    if a.get("net_bj"):
        parts.append(f"计划时间（北京）：{a['net_bj']}")
    if a.get("status"):
        parts.append(f"状态：{a['status']}")
    summary_zh = a.get("summary") or ""
    body = "\n".join(parts) + (("\n\n" + summary_zh) if summary_zh else "")
    card_bits = [b for b in (
        a.get("provider"), a.get("location"),
        (a.get("net_bj") + " 北京") if a.get("net_bj") else "",
    ) if b]
    card = "｜".join(card_bits) or _short(summary_zh, 90)
    return {
        "id": _mk_id("launch", link or a.get("name_en") or "", title),
        "kind": "launch",
        "title": title,
        "title_orig": a.get("name_en") or title,
        "summary": card,
        "body": body.strip(),
        "source": "The Space Devs",
        "published": _to_beijing(pub),
        "published_ts": _ts(pub),
        "tags": ["每日发射"],
        "main_tag": "每日发射",
        "image": _proxy_img(a.get("image") or ""),
        "link": link,
    }


def _norm_debris(a: dict) -> dict:
    """碎片更新（CelesTrak 当日新增编目碎片汇总成一条）。"""
    title = a.get("title") or "碎片更新"
    link = a.get("link") or ""
    pub = a.get("published") or ""
    body = a.get("body") or ""
    return {
        "id": _mk_id("debris", link, title),  # 标题含日期 → 每日唯一
        "kind": "debris",
        "title": title,
        "title_orig": title,
        "summary": a.get("summary") or _short(body, 90),
        "body": body,
        "source": "CelesTrak",
        "published": _to_beijing(pub),
        "published_ts": _ts(pub),
        "tags": ["碎片更新"],
        "main_tag": "碎片更新",
        "image": "",
        "link": link,
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

    # 合并三新栏目库（技术港 / 每日发射 / 碎片更新，国内服务器每日直连入库、已译中文）
    for store, norm in ((_techport_store, _norm_techport),
                        (_launch_store, _norm_launch),
                        (_debris_store, _norm_debris)):
        if store is None:
            continue
        try:
            for a in store.load_recent(days):
                it = norm(a)
                if it["id"] not in seen:
                    seen.add(it["id"])
                    items.append(it)
        except Exception:
            pass

    # 链接去重：三新栏目命中的原文链接，从国际要闻里删掉重复那条（保守：仅链接完全相同）
    others = {it["link"] for it in items
              if it["kind"] in ("techport", "launch", "debris") and it["link"]}
    if others:
        items = [it for it in items if not (it["kind"] == "intl" and it["link"] in others)]

    items.sort(key=lambda x: x["published_ts"], reverse=True)
    index = {it["id"]: it for it in items}
    return items, index


_PREWARM_LOCK = threading.Lock()
_PREWARM_BUSY = False


def _prewarm_images(items: list[dict]) -> None:
    """后台把经 /img 代理的境外图逐张下载落盘，让手机请求时几乎都命中缓存。

    prefetch 对已缓存的图是「查文件存在即返回」的廉价操作，故每次重建重跑只会补抓新图；
    串行执行（不并发）避免给服务器造成冷取高峰。同一时刻只允许一个预热线程。
    """
    global _PREWARM_BUSY
    if _img_proxy is None:
        return
    import urllib.parse as _up
    with _PREWARM_LOCK:
        if _PREWARM_BUSY:
            return
        _PREWARM_BUSY = True
    try:
        seen: set[str] = set()
        for it in items:
            img = it.get("image", "") or ""
            if "/img?" not in img:
                continue
            try:
                q = _up.parse_qs(_up.urlparse(img).query)
            except Exception:
                continue
            u = (q.get("u") or [""])[0]
            r = (q.get("r") or [""])[0]
            if not u or u in seen:
                continue
            seen.add(u)
            try:
                _img_proxy.prefetch(u, r or None, timeout=12.0)
            except Exception:
                pass
    finally:
        _PREWARM_BUSY = False


def _do_build_and_store() -> tuple[list[dict], dict]:
    items, index = _build(_WINDOW_DAYS)
    _CACHE.update(ts=time.time(), items=items, index=index)
    threading.Thread(target=_prewarm_images, args=(items,), daemon=True).start()
    return items, index


def _sources_mtime() -> float:
    """受监控数据源的最新修改时间，用于判断缓存是否落后于今日新内容。

    覆盖：速递缓存 data/cache/*.json + 政要社媒库 social_store.json + 公众号库
    gzh_store.json。任一被写新（每日生成/抓取入库）都会让 mtime 前进。
    """
    m = 0.0
    try:
        for f in WAM_CACHE_DIR.glob("*.json"):
            m = max(m, f.stat().st_mtime)
    except Exception:
        pass
    data_dir = WAM_CACHE_DIR.parent
    for name in ("social_store.json", "gzh_store.json",
                 "techport_store.json", "launch_store.json", "debris_store.json"):
        try:
            m = max(m, (data_dir / name).stat().st_mtime)
        except Exception:
            pass
    return m


def _ensure() -> tuple[list[dict], dict]:
    """返回全量数据集（近 _WINDOW_DAYS 天），days-agnostic。

    构建极快（实测 ~25ms / 数百条），因此采用「按数据新鲜度重建」：
    - TTL 内：直接返回缓存（不做任何磁盘 stat）。
    - TTL 到期且**数据源确有更新**（mtime 比缓存新）：**同步重建**，保证首次打开
      就能看到今日刚生成/入库的内容（不再出现"要再刷一次才更新"）。
    - TTL 到期但数据没变：续期后直接返回旧缓存，不做无谓重建。
    - 完全无缓存（首次）：同步构建。
    """
    now = time.time()
    if (now - _CACHE["ts"] < _CACHE_TTL) and _CACHE["items"]:
        return _CACHE["items"], _CACHE["index"]

    if _CACHE["items"]:
        if _sources_mtime() > _CACHE["ts"]:
            with _BUILD_LOCK:
                # 双检：可能已被其它线程重建
                if _CACHE["items"] and _sources_mtime() <= _CACHE["ts"]:
                    return _CACHE["items"], _CACHE["index"]
                return _do_build_and_store()
        # 数据未变，仅 TTL 老化 → 续期，避免每次请求都 stat
        _CACHE["ts"] = now
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


def week(days: int = 14, kind: str | None = None, offset: int = 0, limit: int = 0) -> dict:
    items, _ = _ensure()
    cutoff = time.time() - days * 86400
    # published_ts==0 表示日期无法解析，保留以免误删
    items = [it for it in items if (it["published_ts"] == 0 or it["published_ts"] >= cutoff)]
    if kind:
        items = [it for it in items if it["kind"] == kind]
    else:
        # 「全部」不展示政要社媒（其有独立栏目），避免与其它内容混排
        items = [it for it in items if it["kind"] != "social"]

    total = len(items)
    kinds = {
        "intl": sum(1 for c in items if c["kind"] == "intl"),
        "gzh": sum(1 for c in items if c["kind"] == "gzh"),
        "douyin": sum(1 for c in items if c["kind"] == "douyin"),
        "social": sum(1 for c in items if c["kind"] == "social"),
        "techport": sum(1 for c in items if c["kind"] == "techport"),
        "launch": sum(1 for c in items if c["kind"] == "launch"),
        "debris": sum(1 for c in items if c["kind"] == "debris"),
    }
    # 分页：limit>0 时只取一页，单页响应小、任何网络都能秒开
    page = items[offset:offset + limit] if limit > 0 else items
    cards = [_card(it) for it in page]
    return {
        "days": days,
        "total": total,
        "count": len(cards),
        "offset": offset,
        "limit": limit,
        "has_more": (offset + len(cards)) < total,
        "items": cards,
        "kinds": kinds,
    }


def detail(item_id: str, days: int = 15) -> dict | None:
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
