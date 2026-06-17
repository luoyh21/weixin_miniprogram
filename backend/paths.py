"""集中管理路径与对 weixin_auto_message 的引用。"""
from __future__ import annotations

import sys
from pathlib import Path

# weixin_miniprogram/backend/paths.py -> 上两级是 workspace
BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BACKEND_DIR.parent                 # weixin_miniprogram/
WORKSPACE_DIR = PROJECT_DIR.parent               # /root/workspace
WAM_DIR = WORKSPACE_DIR / "weixin_auto_message"  # 复用的源项目

# 本后端自己的数据目录（用户、token 密钥等）
DATA_DIR = BACKEND_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# weixin_auto_message 的速递缓存目录（只读复用）
WAM_CACHE_DIR = WAM_DIR / "data" / "cache"


def ensure_wam_importable() -> None:
    """把 weixin_auto_message 加入 sys.path，便于 import src.xxx 复用其模型/配置。"""
    p = str(WAM_DIR)
    if p not in sys.path:
        sys.path.insert(0, p)
