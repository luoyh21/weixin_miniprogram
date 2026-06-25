# weixin_miniprogram — 航天速递微信小程序

与 `weixin_auto_message`（企业微信每日推送）**功能一致、内容同源**，但形态改为**微信小程序**：

- 不再做定时推送，改为**随时阅读近两周**的航天速递；
- 底部 Tab 切换到**问答**页，向大模型提问并获得带链接的回答；
- **我的**页提供注册 / 登录（真实姓名 + 账号 + 密码）；
- 管理员（默认 `lq3525926` 罗一鹤，可多个）在「我的」页会额外出现**管理控制台**：查看 / 修改用户、抖音 Cookie 快捷更新。

内容直接复用 `weixin_auto_message` 已生成的数据（`data/cache/*.json`）与其大模型问答逻辑，不重复抓取。

---

## 目录结构

```
weixin_miniprogram/
├─ backend/                 # 后端（FastAPI 路由，被挂到现有服务的 /api 下）
│  ├─ api.py                # 所有 /api 路由
│  ├─ auth.py               # 注册/登录/鉴权（文件存储 + HMAC token）
│  ├─ news_store.py         # 读取 ../weixin_auto_message/data/cache 聚合近两周
│  ├─ qa.py                 # 复用 summarizer.answer_with_context
│  ├─ douyin_cookie.py      # 抖音容器 Cookie 更新 + 自检
│  ├─ paths.py              # 路径 / 对 weixin_auto_message 的引用
│  └─ data/                 # users.json / secret.key（运行时生成，已 gitignore）
├─ miniprogram/             # 小程序前端
│  ├─ app.{js,json,wxss}
│  ├─ utils/api.js          # wx.request 封装（base = https://links.he-ting.com/api）
│  └─ pages/
│     ├─ news/              # Tab1：近两周速递（国际/公众号/视频 筛选）
│     ├─ detail/            # 文章详情（中文译文原生渲染）
│     ├─ ask/              # Tab2：问答
│     └─ account/           # Tab3：我的（登录/注册 + 管理控制台）
├─ scripts/upload.js        # miniprogram-ci 上传脚本
├─ project.config.json      # appid = wx9561f446d7eb5180
└─ private.wx9561f446d7eb5180.key  # 代码上传私钥（已 gitignore）
```

---

## 后端如何对外提供服务

小程序请求域名固定为 `https://links.he-ting.com`（已在小程序后台「服务器域名」配置）。
该域名当前指向 `weixin_auto_message` 的 FastAPI 服务（监听 `0.0.0.0:8080`）。

因此后端**不单独起进程**，而是把 `backend/api.py` 的 `APIRouter` 以 `/api` 前缀
挂进 `weixin_auto_message/src/server.py`：

```python
from weixin_miniprogram.backend.api import router as _mp_router
app.include_router(_mp_router, prefix="/api")
```

这样小程序通过同一个 HTTPS 域名访问，无需改 nginx / 证书。
重启服务即生效：

```bash
cd /root/workspace/weixin_auto_message
pkill -9 -f "[s]rc.server"      # 注意用括号技巧避免误杀
.venv/bin/python -m src.server  # 后台运行请用 setsid nohup ... &
```

> 后端运行在 `weixin_auto_message/.venv` 里，复用其 fastapi / openai 等依赖，无需额外安装。

---

## API 一览（前缀 `/api`）

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| GET | `/ping` | - | 健康检查 |
| POST | `/auth/register` | - | `{account, real_name, password}` → token+user |
| POST | `/auth/login` | - | `{account, password}`（account 可填账号或真实姓名）|
| GET | `/auth/me` | Bearer | 当前用户 |
| POST | `/auth/change_password` | Bearer | `{old_password, new_password}` |
| GET | `/news/week?days=14&kind=` | - | 近两周新闻（kind: intl/gzh/douyin）|
| GET | `/news/item?id=` | - | 单条详情（含译文正文）|
| POST | `/qa/ask` | Bearer | `{question}` → `{answer}` |
| GET | `/admin/users` | Admin | 用户列表 |
| POST | `/admin/users/update` | Admin | `{account, real_name?, role?, new_password?}` |
| POST | `/admin/users/delete` | Admin | `{account}` |
| GET | `/admin/douyin/status` | Admin | 抖音抓取自检 + 最近作品 |
| POST | `/admin/douyin/cookie` | Admin | `{cookie}` 写入容器并重启 |

鉴权：请求头 `Authorization: Bearer <token>`。

### 管理员判定
默认管理员账号在 `auth.py` 的 `DEFAULT_ADMIN_ACCOUNTS`（或环境变量 `MP_ADMIN_ACCOUNTS`，逗号分隔），
默认 `lq3525926`。该账号**首次注册即自动成为管理员**；之后管理员也可在小程序里把其他用户设为管理员。

---

## 抖音漏检问题（已修复）

**现象**：抖音 6-16 19:18 发布的「实践三十一号」视频没被推送。

**原因**：晚间班次 16:00 抓取（覆盖到 16:00 前），早间班次 08:00 用 12h 窗口（只回溯到前一天 20:00）。
19:18 这条正好落在 **16:00–20:00 的盲区**，两个班次都没覆盖到。

**修复**：把抖音单独的抓取窗口加宽到 24h（`weixin_auto_message/.env` 的 `DOUYIN_WINDOW_HOURS=24`）。
已推送去重（`dedup`）会防止重复发送，因此加宽窗口是安全的。

**Cookie 维护**：抖音抓取依赖容器 `douyin_api`，Cookie 写在容器内
`/app/crawlers/douyin/web/config.yaml`。本小程序「我的 → 管理控制台 → 抖音抓取」可：
- 一键**检测** Cookie 是否有效（看是否能取到作品）；
- 粘贴新 Cookie **一键更新并重启容器**（无需登服务器）。

---

## 上传到微信后台

```bash
cd /root/workspace/weixin_miniprogram
npm i miniprogram-ci
node scripts/upload.js 1.0.0 "首个版本"
```

前置条件：
1. 「微信公众平台 → 开发管理 → 开发设置 → 小程序代码上传」生成的**私钥**已放在
   `private.wx9561f446d7eb5180.key`；
2. 该页面的 **IP 白名单**里加入本服务器出口 IP（否则 CI 上传会被拒）。

也可用「微信开发者工具」直接打开本项目（`project.config.json` 已含 appid）上传。

---

## 小程序后台需要的配置（已具备 / 核对项）

- **服务器域名**：request / uploadFile / downloadFile 均含 `https://links.he-ting.com` ✅
- **消息推送**：URL `http://8.130.209.181`（80 端口）、Token `heting`、安全模式 + JSON —
  由 `weixin_auto_message/src/mp_verify_server.py` 处理 URL 接入验证（已在 80 端口运行）。
- 业务上小程序只用到 `request`，图片用 `<image>`（不校验域名），无需额外业务域名。
