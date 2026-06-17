"""抖音 Cookie 快捷更新（管理员后台用）。

抖音抓取依赖本机 docker 容器 `douyin_api`
（evil0ctal/douyin_tiktok_download_api），其 Cookie 写死在容器内
  /app/crawlers/douyin/web/config.yaml -> TokenManager.douyin.headers.Cookie
容器没有挂载卷，所以更新方式 = 改容器内该行 + 重启容器。

提供：
  status()            -> 探活 + 是否能取到作品（Cookie 是否有效）
  update_cookie(str)  -> 写入新 Cookie 并重启容器，再自检
"""
from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from pathlib import Path

from .paths import ensure_wam_importable

log = logging.getLogger(__name__)

CONTAINER = "douyin_api"
CFG_PATH = "/app/crawlers/douyin/web/config.yaml"


def _docker(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args],
        capture_output=True, text=True, timeout=timeout,
    )


def status() -> dict:
    """返回 {ok, detail, recent:[...]}。"""
    ensure_wam_importable()
    try:
        from src import douyin
    except Exception as e:  # noqa
        return {"ok": False, "detail": f"无法加载抖音模块：{e}", "recent": []}

    import os
    users_raw = os.getenv("DOUYIN_USERS", "")
    ok, detail = douyin.selfcheck(users_raw)
    recent = []
    if ok:
        try:
            from src.douyin import _parse_users, fetch_user_recent
            users = _parse_users(users_raw)
            if users:
                name, sec = users[0]
                for e in fetch_user_recent(sec, name=name, hours=72, count=5):
                    recent.append({"title": e.title, "published": e.published, "source": e.source})
        except Exception as e:  # noqa
            log.warning("recent fetch failed: %s", e)
    return {"ok": ok, "detail": detail, "recent": recent}


def update_cookie(new_cookie: str) -> dict:
    new_cookie = (new_cookie or "").strip().replace("\r", " ").replace("\n", " ")
    new_cookie = re.sub(r"\s+", " ", new_cookie)
    if "sessionid" not in new_cookie and "passport" not in new_cookie:
        raise ValueError("Cookie 看起来不完整（缺少登录态字段），请从已登录的抖音网页完整复制")

    # 1) 读容器内配置
    r = _docker("exec", CONTAINER, "cat", CFG_PATH)
    if r.returncode != 0:
        raise RuntimeError(f"读取容器配置失败：{r.stderr.strip() or r.stdout.strip()}")
    raw = r.stdout

    # 2) 替换第一处 `Cookie:` 行（保持缩进；不加引号，沿用原文件风格）
    lines = raw.split("\n")
    done = False
    for i, l in enumerate(lines):
        if re.match(r"^\s*Cookie:\s", l):
            indent = l[: len(l) - len(l.lstrip())]
            lines[i] = f"{indent}Cookie: {new_cookie}"
            done = True
            break
    if not done:
        raise RuntimeError("未在配置文件里找到 Cookie 行")
    new_raw = "\n".join(lines)

    # 3) 写回容器
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as tf:
        tf.write(new_raw)
        tmp = tf.name
    try:
        cp = _docker("cp", tmp, f"{CONTAINER}:{CFG_PATH}")
        if cp.returncode != 0:
            raise RuntimeError(f"写回容器失败：{cp.stderr.strip()}")
    finally:
        try:
            Path(tmp).unlink()
        except Exception:
            pass

    # 4) 重启容器使其重新加载
    rs = _docker("restart", CONTAINER, timeout=90)
    if rs.returncode != 0:
        raise RuntimeError(f"重启容器失败：{rs.stderr.strip()}")

    # 5) 等待容器起来后自检
    import time
    time.sleep(8)
    st = status()
    st["restarted"] = True
    return st
