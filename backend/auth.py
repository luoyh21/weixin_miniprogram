"""用户注册 / 登录 / 鉴权。

存储：backend/data/users.json（文件级，轻量，够用）。
密码：pbkdf2_hmac(sha256) + 随机盐，不落明文。
令牌：HMAC 签名的无状态 token（account + 过期时间），密钥落 backend/data/secret.key。

字段：
  account    登录账号（唯一，小写不敏感）
  real_name  真实姓名（注册必填）
  role       "admin" | "user"
  pwd_salt / pwd_hash
  created_at / updated_at
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from typing import Any

from .paths import DATA_DIR

USERS_FILE = DATA_DIR / "users.json"
SECRET_FILE = DATA_DIR / "secret.key"

# 内置管理员标识：账号或真实姓名命中任一即视为管理员（首次注册即自动成为管理员，可被其它管理员追加）
DEFAULT_ADMIN_ACCOUNTS = {
    a.strip().lower()
    for a in os.getenv("MP_ADMIN_ACCOUNTS", "lq3525926").split(",")
    if a.strip()
}
DEFAULT_ADMIN_NAMES = {
    n.strip()
    for n in os.getenv("MP_ADMIN_NAMES", "罗一鹤,温跃杰,缪远明").split(",")
    if n.strip()
}


def _is_default_admin(account: str, real_name: str) -> bool:
    return (account or "").strip().lower() in DEFAULT_ADMIN_ACCOUNTS \
        or (real_name or "").strip() in DEFAULT_ADMIN_NAMES

TOKEN_TTL = int(os.getenv("MP_TOKEN_TTL", str(30 * 24 * 3600)))  # 默认 30 天

_lock = threading.Lock()


# ---------------- 底层存储 ----------------
def _load() -> dict[str, dict]:
    if not USERS_FILE.exists():
        return {}
    try:
        return json.loads(USERS_FILE.read_text("utf-8"))
    except Exception:
        return {}


def _save(users: dict[str, dict]) -> None:
    tmp = USERS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(USERS_FILE)


def _secret() -> bytes:
    if SECRET_FILE.exists():
        return SECRET_FILE.read_bytes()
    s = secrets.token_bytes(32)
    SECRET_FILE.write_bytes(s)
    try:
        os.chmod(SECRET_FILE, 0o600)
    except Exception:
        pass
    return s


# ---------------- 密码 ----------------
def _hash_pwd(password: str, salt: str) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return base64.b64encode(dk).decode("ascii")


def _verify_pwd(password: str, salt: str, expected: str) -> bool:
    return hmac.compare_digest(_hash_pwd(password, salt), expected)


# ---------------- token ----------------
def make_token(account: str) -> str:
    exp = int(time.time()) + TOKEN_TTL
    payload = f"{account.lower()}.{exp}"
    sig = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    raw = f"{payload}.{sig}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def parse_token(token: str) -> str | None:
    """校验 token，返回 account（小写）或 None。"""
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        account, exp_s, sig = raw.rsplit(".", 2)
        exp = int(exp_s)
    except Exception:
        return None
    if exp < int(time.time()):
        return None
    payload = f"{account}.{exp}"
    good = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(good, sig):
        return None
    return account


# ---------------- 对外业务 ----------------
def _public(account: str, u: dict) -> dict:
    return {
        "account": account,
        "real_name": u.get("real_name", ""),
        "role": u.get("role", "user"),
        "is_admin": u.get("role") == "admin",
        "created_at": u.get("created_at"),
        "updated_at": u.get("updated_at"),
    }


def register(account: str, real_name: str, password: str) -> dict:
    account = (account or "").strip()
    real_name = (real_name or "").strip()
    if not account or not real_name or not password:
        raise ValueError("账号、真实姓名、密码均为必填")
    if len(password) < 6:
        raise ValueError("密码至少 6 位")
    key = account.lower()
    with _lock:
        users = _load()
        if key in users:
            raise ValueError("该账号已被注册")
        salt = secrets.token_hex(8)
        role = "admin" if _is_default_admin(account, real_name) else "user"
        now = int(time.time())
        users[key] = {
            "account": account,
            "real_name": real_name,
            "role": role,
            "pwd_salt": salt,
            "pwd_hash": _hash_pwd(password, salt),
            "created_at": now,
            "updated_at": now,
        }
        _save(users)
        return _public(account, users[key])


def login(account: str, password: str) -> dict:
    """支持用账号或真实姓名登录。"""
    ident = (account or "").strip()
    if not ident or not password:
        raise ValueError("请输入账号与密码")
    with _lock:
        users = _load()
        key = ident.lower()
        u = users.get(key)
        if u is None:  # 退而求其次：按真实姓名匹配（唯一时）
            matches = [(k, v) for k, v in users.items() if v.get("real_name") == ident]
            if len(matches) == 1:
                key, u = matches[0]
        if u is None or not _verify_pwd(password, u.get("pwd_salt", ""), u.get("pwd_hash", "")):
            raise ValueError("账号或密码错误")
        # 命中内置管理员标识但还不是 admin（如注册时账号填了真实姓名）→ 自动补判并持久化
        if u.get("role") != "admin" and _is_default_admin(u.get("account", key), u.get("real_name", "")):
            u["role"] = "admin"
            u["updated_at"] = int(time.time())
            _save(users)
        return _public(key, u)


def get_user(account: str) -> dict | None:
    users = _load()
    u = users.get((account or "").lower())
    return _public(account.lower(), u) if u else None


def list_users() -> list[dict]:
    users = _load()
    out = [_public(k, v) for k, v in users.items()]
    out.sort(key=lambda x: (x["role"] != "admin", x.get("created_at") or 0))
    return out


def update_user(account: str, *, real_name: str | None = None,
                role: str | None = None, new_password: str | None = None) -> dict:
    key = (account or "").lower()
    with _lock:
        users = _load()
        u = users.get(key)
        if not u:
            raise ValueError("用户不存在")
        if real_name is not None and real_name.strip():
            u["real_name"] = real_name.strip()
        if role in ("admin", "user"):
            u["role"] = role
        if new_password:
            if len(new_password) < 6:
                raise ValueError("密码至少 6 位")
            u["pwd_salt"] = secrets.token_hex(8)
            u["pwd_hash"] = _hash_pwd(new_password, u["pwd_salt"])
        u["updated_at"] = int(time.time())
        _save(users)
        return _public(key, u)


def delete_user(account: str) -> None:
    key = (account or "").lower()
    with _lock:
        users = _load()
        if key in users:
            del users[key]
            _save(users)


def change_own_password(account: str, old_password: str, new_password: str) -> dict:
    key = (account or "").lower()
    with _lock:
        users = _load()
        u = users.get(key)
        if not u or not _verify_pwd(old_password, u.get("pwd_salt", ""), u.get("pwd_hash", "")):
            raise ValueError("原密码错误")
        if len(new_password) < 6:
            raise ValueError("新密码至少 6 位")
        u["pwd_salt"] = secrets.token_hex(8)
        u["pwd_hash"] = _hash_pwd(new_password, u["pwd_salt"])
        u["updated_at"] = int(time.time())
        _save(users)
        return _public(key, u)
