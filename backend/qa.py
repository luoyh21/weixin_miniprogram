"""问答：复用 weixin_auto_message 的 summarizer.answer_with_context。"""
from __future__ import annotations

import logging

from .paths import ensure_wam_importable
from . import news_store

log = logging.getLogger(__name__)


def ask(question: str) -> str:
    question = (question or "").strip()
    if not question:
        raise ValueError("问题不能为空")
    ensure_wam_importable()
    from src.summarizer import answer_with_context  # 复用原项目模型逻辑

    ctx = news_store.latest_qa_context(limit=30)
    return answer_with_context(question, ctx)
