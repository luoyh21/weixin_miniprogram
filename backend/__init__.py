"""微信小程序后端：复用 weixin_auto_message 的数据与模型能力。

设计要点：
- 不重复抓取/推送，直接读取 ../weixin_auto_message/data/cache 里已生成的速递缓存；
- 问答复用 weixin_auto_message.src.summarizer.answer_with_context；
- 以 APIRouter(prefix=/api) 的形式挂到现有 FastAPI 服务（links.he-ting.com），
  这样无需改动 nginx / 证书即可让小程序通过同一 HTTPS 域名访问。
"""
