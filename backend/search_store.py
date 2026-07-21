"""全量搜索：在「近两周实时聚合」之外，补上永久归档里更早的历史记录。

news_store 只聚合近 15 天供列表页展示（受 _WINDOW_DAYS 限制）；本模块在其基础上
扫描 weixin_auto_message/data/news_archive/**（daily.py / gzh_store / social_store /
techport_store / launch_store / debris_store 落库时同步永久归档，不受 14 天清理影响），
让搜索能覆盖自归档功能上线以来的全部历史，而不受列表页展示窗口限制。

复用 news_store 里已验证过的 _norm_* 规整函数，保证同一条内容无论来自「实时聚合」
还是「归档回补」，落地字段结构完全一致（详情页/图片代理等下游无需区分来源）。
"""
from __future__ import annotations

import glob
import json
import logging
import re
import threading
import time
from pathlib import Path

from . import news_store
from .paths import WAM_CACHE_DIR, WAM_DIR

log = logging.getLogger(__name__)

ARCHIVE_DIR = WAM_DIR / "data" / "news_archive"

# 归档里「条目类」kind → 对应的规整函数；digest/cache/ingest/launch_upcoming 是整包快照，
# 不是可直接检索的单条内容，跳过。
_ARCHIVE_KINDS = {
    "intl": news_store._norm_intl,
    "gzh": news_store._norm_gzh,
    "douyin": news_store._norm_dy,
    "social": news_store._norm_social,
    "techport": news_store._norm_techport,
    "launch": news_store._norm_launch,
    "debris": news_store._norm_debris,
}

_CACHE: dict = {"ts": 0.0, "items": [], "index": {}}
_CACHE_TTL = 600  # 搜索索引对新鲜度不敏感（历史内容为主），10 分钟重建一次足够
_LOCK = threading.Lock()


def _iter_archive(kind: str):
    folder = ARCHIVE_DIR / kind
    if not folder.exists():
        return
    for f in sorted(folder.glob("*/*.jsonl")):
        try:
            with f.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except Exception:
                        continue
        except Exception as e:
            log.warning("read archive %s failed: %s", f, e)


def _zh_lookup() -> dict[str, tuple[str, str]]:
    """原文链接 → (中文标题, 中文正文)。

    国际新闻的中文翻译只落在「每日汇总」（cache/digest）里，归档里的 intl 原始条目
    本身只有英文；这里从现存 cache/*.json + 归档 digest/cache 中把已译好的中文回填，
    让老的国际新闻也能被中文关键词搜到（找不到译文时 _norm_intl 会自动回退英文标题）。
    """
    out: dict[str, tuple[str, str]] = {}

    def _collect(spacenews) -> None:
        for a in spacenews or []:
            link = a.get("original_link") or a.get("link") or ""
            if not link or link in out:
                continue
            tz, bz = a.get("title_zh") or "", a.get("body_zh") or ""
            if tz or bz:
                out[link] = (tz, bz)

    try:
        for f in glob.glob(str(WAM_CACHE_DIR / "*.json")):
            try:
                d = json.loads(Path(f).read_text(encoding="utf-8"))
                _collect(d.get("spacenews"))
            except Exception:
                continue
    except Exception:
        pass

    for row in _iter_archive("digest"):
        _collect((row.get("record") or {}).get("spacenews"))
    for row in _iter_archive("cache"):
        _collect((row.get("payload") or {}).get("spacenews"))
    return out


def _index_add(index: dict[str, dict], cutoff: float, kind: str, norm, raw: dict) -> None:
    try:
        it = norm(raw)
    except Exception:
        return
    ts = it.get("published_ts") or 0
    if ts and ts >= cutoff:
        return  # 近窗口内已由实时聚合覆盖
    index.setdefault(it["id"], it)


def _iter_daily_payloads():
    """把「每日整包快照」（cache/digest 归档）展开成逐条 spacenews/opml/douyin。

    有些条目只在整包快照里出现过（比如接入 intl/gzh/douyin 单条归档之前抓到的旧
    内容，或当天没被选中推送、只留在 cache 快照里的），单条归档扫不到它们，
    必须回到整包快照里找——这里的 spacenews 字段已含中文译文，比单条归档更完整。
    """
    for row in _iter_archive("cache"):
        yield row.get("payload") or {}
    for row in _iter_archive("digest"):
        yield row.get("record") or {}


def _build_index() -> tuple[list[dict], dict[str, dict]]:
    live_items, live_index = news_store._ensure()
    index: dict[str, dict] = dict(live_index)
    # 近窗口内的条目已由实时聚合完整覆盖（含正确的中文标题/去重）；归档回补只补
    # 更早的历史，避免同一篇文章因链接改写等差异在索引里出现两次。
    cutoff = time.time() - news_store._WINDOW_DAYS * 86400

    # 1) 每日整包快照（cache/digest）逐条展开：spacenews 已含中文译文，最完整、优先入索引。
    for payload in _iter_daily_payloads():
        for a in payload.get("spacenews") or []:
            _index_add(index, cutoff, "intl", news_store._norm_intl, a)
        for a in payload.get("opml") or []:
            _index_add(index, cutoff, "gzh", news_store._norm_gzh, a)
        for a in payload.get("douyin") or []:
            _index_add(index, cutoff, "douyin", news_store._norm_dy, a)

    # 2) 逐条归档（intl/gzh/douyin/social/techport/launch/debris）兜底补漏：整包快照
    #    只含「当天实际推送/选中」的条目，抓到但未推送的仍只落在这里。
    zh = _zh_lookup()
    for kind, norm in _ARCHIVE_KINDS.items():
        for raw in _iter_archive(kind):
            if kind == "intl" and not (raw.get("title_zh") or raw.get("body_zh")):
                hit = zh.get(raw.get("link") or "")
                if hit:
                    raw = dict(raw)
                    raw["title_zh"], raw["body_zh"] = hit
            _index_add(index, cutoff, kind, norm, raw)

    items = list(index.values())
    items.sort(key=lambda x: x.get("published_ts") or 0, reverse=True)
    return items, index


def _ensure_index() -> tuple[list[dict], dict[str, dict]]:
    now = time.time()
    if now - _CACHE["ts"] < _CACHE_TTL and _CACHE["items"]:
        return _CACHE["items"], _CACHE["index"]
    with _LOCK:
        if now - _CACHE["ts"] < _CACHE_TTL and _CACHE["items"]:
            return _CACHE["items"], _CACHE["index"]
        try:
            items, index = _build_index()
        except Exception:
            log.exception("build search index failed")
            return _CACHE["items"], _CACHE["index"]
        _CACHE.update(ts=time.time(), items=items, index=index)
        return items, index


def warm() -> None:
    """服务启动时后台预热索引，第一次搜索即命中。"""
    try:
        _ensure_index()
    except Exception:
        pass


def get_item(item_id: str) -> dict | None:
    """详情页兜底：近窗口之外的搜索结果，也能在这里查到完整条目。"""
    _, index = _ensure_index()
    return index.get(item_id)


_SPLIT_RE = re.compile(r"[\s,，。;；/|]+")


def _terms(q: str) -> list[str]:
    q = (q or "").strip().lower()
    if not q:
        return []
    parts = [p for p in _SPLIT_RE.split(q) if p]
    return parts or [q]


def _title_blob(it: dict) -> str:
    return " ".join((
        it.get("title", ""), it.get("title_orig", ""), it.get("title_en", ""),
    )).lower()


def _haystack(it: dict, scope: str = "all") -> str:
    if scope == "title":
        return _title_blob(it)
    bits = (
        it.get("title", ""), it.get("title_orig", ""), it.get("title_en", ""),
        it.get("summary", ""), it.get("summary_zh", ""), it.get("summary_en", ""),
        it.get("body", ""), it.get("body_en", ""), it.get("tp_summary", ""),
        it.get("benefits", ""), it.get("benefits_en", ""),
        it.get("source", ""), " ".join(it.get("tags") or []),
    )
    return " ".join(bits).lower()


def _score(it: dict, terms: list[str], scope: str = "all") -> float:
    title = _title_blob(it)
    score = 0.0
    if scope == "title":
        for t in terms:
            if t in title:
                score += 5.0
        return score
    body = " ".join((
        it.get("summary", ""), it.get("summary_zh", ""), it.get("summary_en", ""),
        it.get("body", ""), it.get("body_en", ""), it.get("tp_summary", ""),
        it.get("benefits", ""), it.get("benefits_en", ""),
    )).lower()
    other = (it.get("source", "") + " " + " ".join(it.get("tags") or [])).lower()
    for t in terms:
        if t in title:
            score += 5.0
        if t in body:
            score += 2.0
        if t in other:
            score += 1.0
    return score


def _title_hit(it: dict, terms: list[str]) -> bool:
    title = _title_blob(it)
    return bool(terms) and all(t in title for t in terms)


def search(q: str, kind: str | None = None, sort: str = "time", scope: str = "all",
           offset: int = 0, limit: int = 20) -> dict:
    """模糊搜索：多个词按 AND 匹配（子串），覆盖全部历史（不受 14 天窗口限制）。

    scope: 'all'（标题+正文+来源/标签，默认） | 'title'（只匹配标题，更精确、更快找到确切文章）。
    sort:  'time'（按发布时间倒序，默认） | 'score'（按匹配度，同分再按时间）。

    全文检索时：若存在「标题命中」结果，则只保留标题命中项，避免正文里顺带提到
    关键词的近似条目（如搜「核热推进」却因摘要对比句命中「核电推进」）造成「重复」感。
    """
    scope = scope if scope in ("all", "title") else "all"
    terms = _terms(q)
    items, _ = _ensure_index()
    if kind:
        items = [it for it in items if it["kind"] == kind]

    matched: list[tuple[float, dict]] = []
    if terms:
        for it in items:
            hay = _haystack(it, scope)
            if all(t in hay for t in terms):
                matched.append((_score(it, terms, scope), it))
        if scope == "all":
            titled = [(s, it) for s, it in matched if _title_hit(it, terms)]
            if titled:
                matched = titled

    if sort == "score":
        matched.sort(key=lambda p: (p[0], p[1].get("published_ts") or 0), reverse=True)
    else:
        matched.sort(key=lambda p: p[1].get("published_ts") or 0, reverse=True)

    total = len(matched)
    offset = max(0, offset)
    page = matched[offset:offset + limit] if limit > 0 else matched
    cards = []
    for score, it in page:
        c = news_store._card(it)
        c["score"] = score
        cards.append(c)
    return {
        "q": q,
        "sort": sort,
        "scope": scope,
        "total": total,
        "count": len(cards),
        "offset": offset,
        "limit": limit,
        "has_more": (offset + len(cards)) < total,
        "items": cards,
    }
