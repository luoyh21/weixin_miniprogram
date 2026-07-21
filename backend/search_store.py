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
from .paths import DATA_DIR, WAM_CACHE_DIR, WAM_DIR, ensure_wam_importable

log = logging.getLogger(__name__)

ARCHIVE_DIR = WAM_DIR / "data" / "news_archive"
_EMB_FILE = DATA_DIR / "search_embeddings.json"
_EMB_MODEL = "text-embedding-3-small"
_EMB_BATCH = 64
# 余弦相似度门槛：低于此值视为不相关（text-embedding-3-small 跨语种一般 0.3+ 才有意义）
_SEM_MIN = 0.28
_SEM_TOP = 60  # 语义召回上限，再与关键词结果合并
_FINAL_MIN = 0.38  # 合并打分后的展示门槛，过滤弱相关噪声

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
    """服务启动时后台预热索引 + 语义向量，第一次搜索即命中。"""
    try:
        items, _ = _ensure_index()
        _ensure_embeddings(items, scope="all")
        _ensure_embeddings(items, scope="title")
    except Exception:
        log.exception("search warm failed")


def get_item(item_id: str) -> dict | None:
    """详情页兜底：近窗口之外的搜索结果，也能在这里查到完整条目。"""
    _, index = _ensure_index()
    return index.get(item_id)


# ---------- 关键词（双语）+ 语义向量 ----------

_SPLIT_RE = re.compile(r"[\s,，。;；/|]+")
_HAS_CJK = re.compile(r"[\u4e00-\u9fff]")
_HAS_LATIN = re.compile(r"[A-Za-z]")

# 磁盘向量缓存：{scope: {item_id: {"h": content_hash, "v": [float, ...]}}}
_EMB_DISK: dict[str, dict] = {}
_EMB_LOCK = threading.Lock()
_Q_EMB_CACHE: dict[str, tuple[float, list[float]]] = {}  # q|scope -> (ts, vec)
_Q_EMB_TTL = 600


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


def _lex_score(it: dict, terms: list[str], scope: str = "all") -> float:
    if not terms:
        return 0.0
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


def _doc_text(it: dict, scope: str = "all") -> str:
    """写入 embedding 的中英混合短文本（控制长度以控成本）。"""
    if scope == "title":
        parts = [it.get("title"), it.get("title_en"), it.get("title_orig")]
        return " | ".join(p for p in parts if p)[:800]
    parts = [
        it.get("title"), it.get("title_en"), it.get("title_orig"),
        it.get("summary_zh"), it.get("summary"), it.get("summary_en"),
        it.get("tp_summary"), it.get("benefits"),
        (it.get("body") or "")[:360],
        (it.get("body_en") or "")[:360],
        " ".join(it.get("tags") or []),
    ]
    return " | ".join(p for p in parts if p)[:1800]


def _content_hash(text: str) -> str:
    import hashlib
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]


def _load_emb_disk() -> None:
    global _EMB_DISK
    if _EMB_DISK:
        return
    if _EMB_FILE.exists():
        try:
            _EMB_DISK = json.loads(_EMB_FILE.read_text(encoding="utf-8"))
            if not isinstance(_EMB_DISK, dict):
                _EMB_DISK = {}
        except Exception:
            _EMB_DISK = {}
    else:
        _EMB_DISK = {}


def _save_emb_disk() -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _EMB_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_EMB_DISK, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_EMB_FILE)
    except Exception as e:
        log.warning("save embeddings failed: %s", e)


def _openai_client():
    ensure_wam_importable()
    from src.summarizer import client  # type: ignore
    return client()


def _embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    cli = _openai_client()
    out: list[list[float]] = []
    for i in range(0, len(texts), _EMB_BATCH):
        chunk = texts[i:i + _EMB_BATCH]
        resp = cli.embeddings.create(model=_EMB_MODEL, input=chunk)
        # API 保证按 index 排序，但仍按 index 取更稳
        by_idx = {d.index: d.embedding for d in resp.data}
        out.extend(by_idx[j] for j in range(len(chunk)))
    return out


def _ensure_embeddings(items: list[dict], scope: str = "all") -> dict[str, list[float]]:
    """为条目补齐向量；命中磁盘缓存则跳过 API。返回 {id: vec}。"""
    _load_emb_disk()
    bucket = _EMB_DISK.setdefault(scope, {})
    need_ids: list[str] = []
    need_texts: list[str] = []
    hashes: dict[str, str] = {}
    for it in items:
        iid = it.get("id") or ""
        if not iid:
            continue
        text = _doc_text(it, scope)
        h = _content_hash(text)
        hashes[iid] = h
        cur = bucket.get(iid)
        if not cur or cur.get("h") != h or not cur.get("v"):
            need_ids.append(iid)
            need_texts.append(text or iid)

    if need_texts:
        with _EMB_LOCK:
            # 双检：避免并发重复打 API
            still_ids, still_texts = [], []
            for iid, text in zip(need_ids, need_texts):
                cur = bucket.get(iid)
                if not cur or cur.get("h") != hashes[iid] or not cur.get("v"):
                    still_ids.append(iid)
                    still_texts.append(text)
            if still_texts:
                try:
                    log.info("embed %d docs scope=%s", len(still_texts), scope)
                    vecs = _embed_texts(still_texts)
                    for iid, vec in zip(still_ids, vecs):
                        bucket[iid] = {"h": hashes[iid], "v": vec}
                    _save_emb_disk()
                except Exception:
                    log.exception("embed docs failed scope=%s", scope)

    return {iid: bucket[iid]["v"] for iid in hashes if bucket.get(iid, {}).get("v")}


def _embed_query(q: str, scope: str) -> list[float] | None:
    key = scope + "|" + q.strip().lower()
    now = time.time()
    hit = _Q_EMB_CACHE.get(key)
    if hit and now - hit[0] < _Q_EMB_TTL:
        return hit[1]
    try:
        vec = _embed_texts([q.strip()])[0]
        _Q_EMB_CACHE[key] = (now, vec)
        return vec
    except Exception:
        log.exception("embed query failed")
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / ((na ** 0.5) * (nb ** 0.5))


# 检索专用名词对照（优先于 LLM 互译，避免「星落→Falling Stars」这类误译）
_TERM_ALIASES: dict[str, str] = {
    "星落": "starfall",
    "starfall": "星落",
    "星舰": "starship",
    "starship": "星舰",
    "星链": "starlink",
    "starlink": "星链",
    "猎鹰9": "falcon 9",
    "猎鹰 9": "falcon 9",
    "falcon 9": "猎鹰9",
    "falcon9": "猎鹰9",
}


def _alias_alt(q: str) -> str:
    """整词/整句命中对照表则直接返回对译；否则在查询中替换已知专名。"""
    q0 = (q or "").strip()
    if not q0:
        return ""
    low = q0.lower()
    if low in _TERM_ALIASES:
        return _TERM_ALIASES[low]
    if q0 in _TERM_ALIASES:
        return _TERM_ALIASES[q0]
    # 子串替换（长短语优先）
    out = q0
    for src, dst in sorted(_TERM_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if _HAS_CJK.search(src):
            if src in out:
                out = out.replace(src, dst)
        else:
            # 英文按词边界忽略大小写替换
            out = re.sub(re.escape(src), dst, out, flags=re.IGNORECASE)
    return out if out != q0 else ""


def _translate_query_bilingual(q: str) -> str:
    """中英互译扩展查询词：中文→英、英文→中，失败则返回空串。

    先查专有名词对照表（如 星落↔starfall），再回退 LLM。
    """
    q = (q or "").strip()
    if not q:
        return ""
    aliased = _alias_alt(q)
    if aliased:
        return aliased
    has_cjk = bool(_HAS_CJK.search(q))
    has_lat = bool(_HAS_LATIN.search(q))
    # 已中英混杂则不再译
    if has_cjk and has_lat:
        return ""
    try:
        ensure_wam_importable()
        from src.config import SETTINGS  # type: ignore
        from src.summarizer import client  # type: ignore
        if has_cjk:
            sys_p = (
                "Translate the search query into English for news retrieval. "
                "Output 1-6 English words only — a faithful translation, no extra words like 'aerospace'/'news'."
            )
        else:
            sys_p = (
                "把检索词译成简体中文，只输出1–8个字的忠实译文，不要添加「航天」「资讯」等多余词，不要引号。"
            )
        resp = client().chat.completions.create(
            model=SETTINGS.openai_model,
            messages=[
                {"role": "system", "content": sys_p},
                {"role": "user", "content": q},
            ],
            temperature=0.0,
            max_tokens=40,
        )
        out = (resp.choices[0].message.content or "").strip().strip('"\'')
        if out.lower() == q.lower():
            return ""
        return out
    except Exception as e:
        log.warning("bilingual translate failed: %s", e)
        return ""


def _lexical_match(items: list[dict], queries: list[str], scope: str) -> dict[str, float]:
    """多查询 OR：任一查询的全部 terms 命中即入选；分取各查询最高分。"""
    scores: dict[str, float] = {}
    for q in queries:
        terms = _terms(q)
        if not terms:
            continue
        for it in items:
            hay = _haystack(it, scope)
            if all(t in hay for t in terms):
                s = _lex_score(it, terms, scope)
                iid = it["id"]
                if s > scores.get(iid, 0):
                    scores[iid] = s
    return scores


def search(q: str, kind: str | None = None, sort: str = "time", scope: str = "all",
           offset: int = 0, limit: int = 20) -> dict:
    """语义搜索（主）+ 双语关键词（辅）。

    - 用 text-embedding-3-small 对查询与条目（中英混合字段）做向量相似度，天然支持跨语种；
    - 同时把查询中英互译后做子串匹配，补足专有名词精确命中；
    - 向量服务失败时自动降级为「双语关键词」检索。

    scope: 'all' | 'title'；sort: 'time' | 'score'（相关度）。
    """
    scope = scope if scope in ("all", "title") else "all"
    items, _ = _ensure_index()
    if kind:
        items = [it for it in items if it["kind"] == kind]
    q = (q or "").strip()
    if not q or not items:
        return {
            "q": q, "sort": sort, "scope": scope, "mode": "empty",
            "total": 0, "count": 0, "offset": offset, "limit": limit,
            "has_more": False, "items": [],
        }

    # 双语扩展（中↔英）
    q2 = _translate_query_bilingual(q)
    queries = [q] + ([q2] if q2 else [])

    # 语义召回（原查询 + 对照/互译查询取较高相似度，保证「星落」也能命中 starfall 文档）
    mode = "semantic"
    sem_scores: dict[str, float] = {}
    qvecs = []
    v0 = _embed_query(q, scope)
    if v0 is not None:
        qvecs.append(v0)
    if q2:
        v1 = _embed_query(q2, scope)
        if v1 is not None:
            qvecs.append(v1)
    if qvecs:
        id2vec = _ensure_embeddings(items, scope=scope)
        scored = []
        for it in items:
            vec = id2vec.get(it["id"])
            if not vec:
                continue
            sim = max(_cosine(qv, vec) for qv in qvecs)
            if sim >= _SEM_MIN:
                scored.append((sim, it["id"]))
        scored.sort(reverse=True)
        for sim, iid in scored[:_SEM_TOP]:
            sem_scores[iid] = sim
    else:
        mode = "bilingual_lexical"

    lex_scores = _lexical_match(items, queries, scope)
    if not sem_scores and lex_scores:
        mode = "bilingual_lexical"

    # 合并：有语义用 0.75*sem + 0.25*归一化词项；纯词项则用词项分
    id2it = {it["id"]: it for it in items}
    lex_max = max(lex_scores.values()) if lex_scores else 1.0
    matched: list[tuple[float, dict]] = []
    seen: set[str] = set()
    for iid, sim in sem_scores.items():
        it = id2it.get(iid)
        if not it:
            continue
        lex_n = (lex_scores.get(iid, 0.0) / lex_max) if lex_max else 0.0
        final = 0.75 * sim + 0.25 * lex_n
        # 精确标题命中额外加分
        if lex_scores.get(iid, 0) >= 5.0:
            final += 0.08
        matched.append((final, it))
        seen.add(iid)
    for iid, ls in lex_scores.items():
        if iid in seen:
            continue
        it = id2it.get(iid)
        if not it:
            continue
        # 仅关键词命中：映射到与语义分相近的量级
        matched.append((0.40 + 0.45 * (ls / lex_max if lex_max else 0), it))

    # 弱相关过滤：精确标题命中（lex>=5）始终保留
    matched = [
        (s, it) for s, it in matched
        if s >= _FINAL_MIN or lex_scores.get(it["id"], 0) >= 5.0
    ]

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
        c["score"] = round(float(score), 4)
        cards.append(c)
    return {
        "q": q,
        "q_alt": q2,
        "sort": sort,
        "scope": scope,
        "mode": mode,
        "total": total,
        "count": len(cards),
        "offset": offset,
        "limit": limit,
        "has_more": (offset + len(cards)) < total,
        "items": cards,
    }
