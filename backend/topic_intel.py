"""专题情报：不定期更新的「指定专题」深度情报集合。

设计要点
- 与每日速递解耦：数据单独存放在 backend/data/topics/<id>.json。
- 「发起/重新生成」时收集近 N 年（默认 3）的相关内容，产出约 18 条。
- 尽量少用 LLM：采用人工策展 + 关键词打分/分类的策略（运行时零 LLM 调用）。
  当前服务器出口网络受限（搜索引擎/部分源站不可达），自动多源爬取不可靠，
  因此本专题采用「策展数据集 + 关键词归类」方式落库，保证结果稳定可复现。
- 支持企业微信单独推送：先私推管理员（LuoYiHe），确认后再全员（@all）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import os

from .paths import DATA_DIR, WAM_DIR, ensure_wam_importable

log = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))
TOPICS_DIR = DATA_DIR / "topics"
TOPICS_DIR.mkdir(parents=True, exist_ok=True)

# 专题翻译落地页（持久，不参与每日 news_pages 的轮转/清理）
TOPIC_PAGES_DIR = WAM_DIR / "data" / "topic_pages"
TOPIC_PAGES_DIR.mkdir(parents=True, exist_ok=True)


def _public_base() -> str:
    return (os.getenv("PUBLIC_BASE_URL", "") or "").rstrip("/") or "https://links.he-ting.com"

ADMIN_PUSH_USER = "LuoYiHe"  # 私推目标（与 run_scheduler 一致）
ALL_PUSH_USER = "@all"


def _now_iso() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M")


def _item_id(url: str, title: str) -> str:
    return hashlib.sha1(f"{url}|{title}".encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 策展数据集：太空拖车（轨道转移与在轨服务）
# 围绕「总体设计」与「专业技术」，覆盖国内 / 国外，时间跨度近 3 年。
# 每条：title / source / url / published / region(国内|国外) / aspect(总体设计|专业技术) / summary / extra_tags
# ---------------------------------------------------------------------------
SPACE_TUG_TITLE = "太空拖车 · 轨道转移与在轨服务"
SPACE_TUG_INTRO = (
    "“太空拖车”（Space Tug / 轨道转移飞行器 OTV）是一类介于火箭上面级与卫星之间的航天器，"
    "可把载荷从一个轨道转移到另一个轨道，并衍生出在轨部署、延寿、加注、维修与碎片清理等在轨服务能力，"
    "被视为商业航天“最后一公里交付”和太空可持续运营的关键基础设施。"
    "本专题汇集近 3 年国内外在总体设计与专业技术两条主线上的代表性进展。"
)

SPACE_TUG_ITEMS = [
    # ---------------- 国外 · 总体设计 ----------------
    {
        "title": "Impulse Space 公布 Helios 高能上面级：一天内把 5 吨载荷从 LEO 送到 GEO",
        "source": "SpaceNews",
        "url": "https://spacenews.com/impulse-space-announces-plan-to-develop-high-energy-kick-stage/",
        "published": "2024-01-17",
        "region": "国外",
        "aspect": "总体设计",
        "summary": (
            "Impulse Space 在 Mira 之外推出更大的 Helios 上面级，采用液氧/甲烷的 Deneb 发动机，"
            "推力约 15000 磅力，可在不到一天内将最重约 5000 公斤的卫星由近地轨道送入地球静止轨道，"
            "兼容 Falcon 9/Vulcan 等中型火箭，计划 2026 年首飞。代表了大运力“太空拖车”的总体设计方向。"
        ),
        "extra_tags": ["上面级", "LEO-GEO"],
    },
    {
        "title": "Impulse Space 获 1.5 亿美元融资，扩产 Mira / Helios 轨道转移飞行器",
        "source": "CNBC",
        "url": "https://www.cnbc.com/2024/10/01/impulse-spacecraft-delivery-startup-raises-150-million-led-by-founders-fund.html",
        "published": "2024-10-01",
        "region": "国外",
        "aspect": "总体设计",
        "summary": (
            "由前 SpaceX 推进负责人 Tom Mueller 创立的 Impulse Space 完成 1.5 亿美元融资（Founders Fund 领投），"
            "用于规模化生产小型 Mira 与大型 Helios 两款“太空拖车”。Mira 首飞（LEO Express-1，2023.11）成功部署小卫星，"
            "公司规划年产至少 10 台 Mira，并推进 GEO 拼车服务。体现该赛道的产业化进程。"
        ),
        "extra_tags": ["商业航天", "融资"],
    },
    {
        "title": "Starfish Space 的 Otter 在轨服务航天器获超 1 亿美元 B 轮融资",
        "source": "行业报道 / BlacKnight Space Labs",
        "url": "https://blacknightspacelabs.com/blog/satellite-servicing-market-on-orbit-services-competitive-landscape",
        "published": "2026-04",
        "region": "国外",
        "aspect": "总体设计",
        "summary": (
            "由前蓝色起源工程师创立的 Starfish Space 推进 Otter 多任务在轨服务飞行器，主打对接、位置保持与受控离轨，"
            "2026 年完成超 1 亿美元 B 轮融资并拿到太空军合同。显示“碎片清除/在轨服务即业务”正从政府验证走向可重复的商业模式。"
        ),
        "extra_tags": ["在轨服务", "离轨"],
    },
    {
        "title": "欧洲 ArianeGroup ASTRIS 上面级与历代 OTV 概念综述",
        "source": "DLR / EUCASS",
        "url": "https://elib.dlr.de/221991/1/EUCASS-25_Overview_of_Past_and_Contemporary_Concepts_for_OTVs.pdf",
        "published": "2025",
        "region": "国外",
        "aspect": "总体设计",
        "summary": (
            "DLR 在 EUCASS 的综述系统梳理了历史与当代轨道转移飞行器（OTV）的分类：多数面向地球轨道任务、"
            "偏好液体推进、常以“踢级（kick stage）”形式随发射任务上行。Ariane 6 配套的 ASTRIS 踢级可执行 GTO→GEO、"
            "星座多轨部署与探测任务，载荷可达 4500 公斤，是欧洲“太空拖车”的总体设计代表。"
        ),
        "extra_tags": ["踢级", "欧洲"],
    },
    {
        "title": "NASA《小航天器技术现状(SOA)》：OTV/OMV 谱系与“太空拖车”术语演变",
        "source": "NASA",
        "url": "https://www.nasa.gov/wp-content/uploads/2026/05/10-soa-launch-2026-final.pdf",
        "published": "2026-05",
        "region": "国外",
        "aspect": "总体设计",
        "summary": (
            "NASA 年度《State of the Art》报告系统盘点了 Impulse Mira/Helios、Momentus Vigoride、D-Orbit ION、"
            "Firefly Elytra（含 SHERPA）、Rocket Lab Photon、Atomos Quark 等轨道转移/机动飞行器，并指出业界正逐步"
            "用“在轨物流/OTV/OMV”替代旧称“space tug”。是把握全球总体技术格局的权威参考。"
        ),
        "extra_tags": ["综述", "产品谱系"],
    },
    # ---------------- 国外 · 专业技术 ----------------
    {
        "title": "诺斯罗普·格鲁曼 MEV-1/MEV-2：商业 GEO 卫星对接延寿的首例",
        "source": "eoPortal",
        "url": "https://www.eoportal.org/satellite-missions/mev-1",
        "published": "2024-05-23",
        "region": "国外",
        "aspect": "专业技术",
        "summary": (
            "任务延寿飞行器 MEV-1/MEV-2 分别于 2019/2020 年发射，与燃料将尽的 Intelsat 卫星对接后接管其推进与姿控。"
            "MEV-1 为 IS-901 提供 5 年延寿后于 2024 年释放至坟墓轨道转入下一目标；MEV-2 与 IS-10-02 续约 4 年。"
            "验证了 GEO 非合作/半合作对接与接管控制的工程可行性。"
        ),
        "extra_tags": ["延寿", "GEO对接"],
    },
    {
        "title": "诺·格 MRV + 任务延寿舱(MEP)：机械臂在轨安装“电推背包”",
        "source": "Via Satellite",
        "url": "https://www.satellitetoday.com/technology/2026/05/19/northrop-grummans-first-mrv-readies-for-summer-launch-to-expand-the-space-servicing-toolkit/",
        "published": "2026-05-19",
        "region": "国外",
        "aspect": "专业技术",
        "summary": (
            "新一代任务机器人飞行器 MRV 配备海军研究实验室研制的机械臂，可对 GEO 卫星做巡检、维修，"
            "并安装约 350 公斤的任务延寿舱（MEP）——一种贴附在卫星发动机喷管上、用电推提供约 6 年额外寿命的“喷气背包”。"
            "代表在轨服务从“整星接管”走向“模块化、机械臂作业”。"
        ),
        "extra_tags": ["机械臂", "电推延寿"],
    },
    {
        "title": "诺·格 PRM 加注接口成为美军首个优选标准，并研制 GAS-T 加油星",
        "source": "SpaceNews",
        "url": "https://spacenews.com/northrop-grummans-orbital-refueling-port-selected-for-u-s-military-satellites/",
        "published": "2024-01",
        "region": "国外",
        "aspect": "专业技术",
        "summary": (
            "美太空军太空系统司令部把诺·格的被动加注模块（PRM）选为军星首个优选在轨加注接口；公司还在 ESPAStar-D 平台上"
            "研制可携带约 1000 公斤肼的地球同步加油星 GAS-T。在轨加注接口标准化正在重塑卫星采购与运营方式。"
        ),
        "extra_tags": ["在轨加注", "接口标准"],
    },
    {
        "title": "Orbit Fab RAFTI 加注接口与 AIAA S-157-2025 在轨流体传输标准",
        "source": "SpaceNews / AIAA",
        "url": "https://spacenews.com/northrop-grummans-orbital-refueling-port-selected-for-u-s-military-satellites/",
        "published": "2024-2025",
        "region": "国外",
        "aspect": "专业技术",
        "summary": (
            "初创公司 Orbit Fab 推动 RAFTI 快接式流体传输接口与在轨推进剂补给站，2024 年被太空军接纳为军星加注接口之一；"
            "AIAA 于 2025 年 3 月发布 ANSI/AIAA S-157-2025，规定在轨可储存流体传输系统的最佳实践，为“太空加油”建立行业标准。"
        ),
        "extra_tags": ["在轨加注", "标准"],
    },
    {
        "title": "Astroscale ADRAS-J 抵近观测与 ELSA-M 碎片清除：RPO 与机械捕获",
        "source": "行业报道 / BlacKnight Space Labs",
        "url": "https://blacknightspacelabs.com/blog/satellite-servicing-market-on-orbit-services-competitive-landscape",
        "published": "2024",
        "region": "国外",
        "aspect": "专业技术",
        "summary": (
            "Astroscale 的 ADRAS-J（2024 发射）首次以自主交会抵近（RPO）安全接近并环绕表征一块在轨大型碎片；"
            "后续 ELSA-M 将演示对带磁性对接板合作目标的离轨清除。代表非合作目标交会抵近与捕获这一在轨服务核心技术。"
        ),
        "extra_tags": ["碎片清除", "RPO"],
    },
    {
        "title": "低推力 LEO→GEO 多飞行器转移网络的燃料最优设计",
        "source": "Utah State Univ. / SmallSat",
        "url": "https://digitalcommons.usu.edu/cgi/viewcontent.cgi?article=6130&context=smallsat",
        "published": "2024",
        "region": "国外",
        "aspect": "专业技术",
        "summary": (
            "该论文研究连续小推力下从 LEO 向 GEO 转移载荷的单/多飞行器服务架构，联合优化推进剂补给站布置与服务器轨迹，"
            "利用 J2 摄动引起的升交点进动降低推进剂消耗，并建立燃料消耗与飞行时间的解析模型。属于“太空拖车”轨迹与任务规划的前沿专业技术。"
        ),
        "extra_tags": ["轨迹优化", "电推进"],
    },
    # ---------------- 国内 · 总体设计 ----------------
    {
        "title": "星辰空间“太空拖车”随谷神星一号入轨：国内首个商业火箭末级留轨平台",
        "source": "中国航天 (chinaerospace.com)",
        "url": "https://www.chinaerospace.com/article/25149",
        "published": "2024-06-06",
        "region": "国内",
        "aspect": "总体设计",
        "summary": (
            "2024 年 6 月，北京星辰空间联合科工二院 206 所、星河动力研制的“太空拖车”随谷神星一号入轨，"
            "用于解决一箭多星不同轨位的“最后一公里”精确入轨。该平台为国内首个商业火箭末级留轨试验平台，"
            "计划在轨超 6 个月，验证轨道调相/维持与 400W 纯氪霍尔电推，为在轨制造、维修、操控、延寿打基础。"
        ),
        "extra_tags": ["霍尔电推", "末级留轨"],
    },
    {
        "title": "未来宇航发布 FX“锋行”系列空间飞行器：国内首个服务组网与在轨业务的商业平台",
        "source": "网易 / 36氪",
        "url": "https://c.m.163.com/news/a/KRMAS0FK05118DFD.html",
        "published": "2026-04-28",
        "region": "国内",
        "aspect": "总体设计",
        "summary": (
            "未来宇航在雄安发布 FX“锋行”系列空间飞行器，面向 300kg–4000kg 任务，主攻高精度姿轨控、柔性承载与快速轨道响应，"
            "兼具火箭变轨与卫星在轨驻留能力，可承担入轨部署、在轨服务与离轨处置，被类比为“太空 4S 店”，"
            "定位为支撑在轨服务工程化落地的基础平台。"
        ),
        "extra_tags": ["太空4S店", "平台化"],
    },
    {
        "title": "太空拖船科普：概念、“最后一公里”交付与“拖船即服务”模式",
        "source": "澎湃新闻",
        "url": "https://www.thepaper.cn/newsDetail_forward_26182492",
        "published": "2024",
        "region": "国内",
        "aspect": "总体设计",
        "summary": (
            "文章厘清太空拖船的定义：把太空物体从一个轨道转移到另一个轨道（如 LEO→GTO/月球转移/逃逸轨道），"
            "类似港口大马力拖船，拖到位即“分手”。卫星依托拖船可削减自身变轨能力以降本，由此催生“太空拖船即服务”"
            "这一商业“最后一英里”交付新模式。适合作为专题入门导读。"
        ),
        "extra_tags": ["科普", "最后一公里"],
    },
    # ---------------- 国内 · 专业技术 ----------------
    {
        "title": "湖科大二号：国内商业航天首颗柔性机械臂在轨操作验证星",
        "source": "网易号",
        "url": "https://www.163.com/dy/article/KOUFUVTS0556EYKS.html",
        "published": "2026",
        "region": "国内",
        "aspect": "专业技术",
        "summary": (
            "由湖南科技大学与苏州三垣航天联合研制的“湖科大二号”，是我国商业航天首颗配置柔性机械臂的在轨操作技术验证星，"
            "任务目标为验证机械臂的伸展、抓取、搬运、释放等能力，为后续在轨维修、加注、升级与碎片捕获等复杂在轨服务奠定基础。"
        ),
        "extra_tags": ["柔性机械臂", "在轨操作"],
    },
    {
        "title": "实践二十五号与实践二十一号完成我国首次卫星在轨加注",
        "source": "公开报道",
        "url": "https://www.chinaerospace.com/",
        "published": "2025-11",
        "region": "国内",
        "aspect": "专业技术",
        "summary": (
            "实践二十五号（2025 年初发射，面向燃料补加与延寿技术验证）于 2025 年 11 月与实践二十一号交会对接，"
            "完成我国首次卫星在轨加注任务，打通了非合作/半合作目标交会对接与推进剂传输的关键技术链路，是“太空加油”的里程碑。"
        ),
        "extra_tags": ["在轨加注", "交会对接"],
    },
    {
        "title": "驭星三号 06 星“太空加油站”：柔性机械臂模拟燃料加注在轨验证",
        "source": "新华网",
        "url": "https://www.news.cn/sci-tech/20260424/edd54bfc4d8b43e284cb80cd36b16d2f/c.html",
        "published": "2026-04",
        "region": "国内",
        "aspect": "专业技术",
        "summary": (
            "由航天驭星旗下苏州三垣航天研制的驭星三号 06 星（外号“太空加油站”）发射入轨，作为我国首颗配置柔性机械臂的"
            "商用试验星，在数百公里高空完成柔性机械臂模拟燃料加注等高难度在轨操作，验证了大量在轨服务关键技术。"
        ),
        "extra_tags": ["模拟加注", "柔性机械臂"],
    },
    {
        "title": "轻舟试验飞船：黏附器完成非合作目标捕获与拖曳演示",
        "source": "中华网 / 中科院微小卫星创新院",
        "url": "https://tech.china.com/article/20260416/202604161848337.html",
        "published": "2026-04",
        "region": "国内",
        "aspect": "专业技术",
        "summary": (
            "2026 年 3 月发射的轻舟试验飞船（4.2 吨、一体化单舱、设计寿命 3 年）完成 200→600 公里大范围轨道机动，"
            "累计在轨点火超 3000 秒；其搭载的“黏附器”完成非合作目标捕获与拖曳演示，为空间碎片清理与物资转运提供了新的技术路径。"
        ),
        "extra_tags": ["非合作捕获", "拖曳"],
    },
    # ---------------- 追加（扩充至 24 条）----------------
    {
        "title": "Momentus 联手 Astroscale，向 NASA 提出哈勃望远镜重轨/延寿的商业方案",
        "source": "Momentus / 投资者新闻",
        "url": "https://investors.momentus.space/news-releases/news-release-details/need-lift-astroscale-and-momentus-team-offer-nasa-commercial",
        "published": "2023",
        "region": "国外",
        "aspect": "总体设计",
        "summary": (
            "Momentus 以其在轨的 Vigoride 服务飞行器结合 Astroscale 的交会抵近与机器人捕获（RPOD）能力，"
            "向 NASA 提出用小型在轨服务飞行器抵近、捕获并把哈勃望远镜抬升约 50 公里、并清理周边碎片的低成本商业方案，"
            "展示“太空拖车 + 在轨服务”叠加的总体设计思路。"
        ),
        "extra_tags": ["哈勃延寿", "RPOD"],
    },
    {
        "title": "蓝色起源 Blue Ring：可托管 3 吨级载荷的多用途在轨机动平台",
        "source": "Ars Technica",
        "url": "https://arstechnica.com/space/2024/01/meet-helios-a-new-class-of-space-tug-with-some-real-muscle/",
        "published": "2024-01",
        "region": "国外",
        "aspect": "总体设计",
        "summary": (
            "蓝色起源公布 Blue Ring 航天平台，可托管最高约 3 吨载荷，提供在轨机动、转移与托管服务，"
            "瞄准更大质量卫星的轨道转移与在轨基础设施市场，是与 Impulse Helios 并行的大运力“太空拖车”总体设计路线。"
        ),
        "extra_tags": ["在轨平台", "大运力"],
    },
    {
        "title": "Rocket Lab Photon 与 Firefly Elytra(SHERPA)：在轨机动飞行器产品谱系",
        "source": "NASA SOA 报告",
        "url": "https://www.nasa.gov/wp-content/uploads/2026/05/10-soa-launch-2026-final.pdf",
        "published": "2026-05",
        "region": "国外",
        "aspect": "总体设计",
        "summary": (
            "Rocket Lab 基于电子火箭衍生的 Photon 上面级可把载荷送入电子火箭单独难以到达的轨道，并可挂载于其它火箭的 ESPA 口；"
            "Firefly 在收购 Spaceflight(SHERPA) 后推出 Elytra 系列（Dawn/Dark），覆盖 LEO 内机动到月球轨道转移，"
            "体现成熟商业“太空拖车”的产品化与谱系化。"
        ),
        "extra_tags": ["上面级", "产品谱系"],
    },
    {
        "title": "Atomos(Katalyst) Quark-LITE：小卫星在轨交会对接与加注能力验证",
        "source": "NASA SOA 报告",
        "url": "https://www.nasa.gov/wp-content/uploads/2026/05/10-soa-launch-2026-final.pdf",
        "published": "2024",
        "region": "国外",
        "aspect": "专业技术",
        "summary": (
            "Atomos（现并入 Katalyst Space Technologies）的 Quark 系列面向小卫星提供部署、变轨（升轨/调相/变倾角）、"
            "交会、对接与延寿等在轨服务。首版 Quark-LITE 于 2024 年春发射，尽管出现通信与翻滚问题，仍演示了交会、对接与加注能力，"
            "为后续 GEO 任务奠定专业技术基础。"
        ),
        "extra_tags": ["交会对接", "加注验证"],
    },
    {
        "title": "EUCASS 论文：上面级/踢级推进系统参数敏感性与多轨注入轨迹优化",
        "source": "EUCASS 2023",
        "url": "https://www.eucass.eu/doi/EUCASS2023-927.pdf",
        "published": "2023",
        "region": "国外",
        "aspect": "专业技术",
        "summary": (
            "该研究以踢级系统把 6U 立方星分发到 8 个不同近地轨道为例，分析推进剂选型、发动机故障再点火、最小比冲与推进系统质量"
            "对最优轨迹与任务成功率的影响，给出完成全部入轨所需的最小比冲约 210 秒等结论，是“太空拖车”推进与轨迹规划的专业技术参考。"
        ),
        "extra_tags": ["推进", "轨迹优化"],
    },
    {
        "title": "新华网：我国商业航天加速，在轨服务从技术验证迈向规模化",
        "source": "新华网",
        "url": "https://www.news.cn/sci-tech/20260424/edd54bfc4d8b43e284cb80cd36b16d2f/c.html",
        "published": "2026-04-24",
        "region": "国内",
        "aspect": "总体设计",
        "summary": (
            "新华网综述指出，2025 年我国商业航天完成 50 次发射、占全年宇航发射的 54%，在轨服务（柔性机械臂、模拟加注、"
            "非合作目标捕获拖曳等）密集取得突破，空间飞行器/“太空拖车”作为产业链关键补位，正推动商业航天从技术验证走向规模化部署。"
        ),
        "extra_tags": ["商业航天", "产业"],
    },
]


_PUB_YEAR_RE = re.compile(r"(?:19|20)\d{2}")


def _published_key(s: str) -> tuple[int, int, int]:
    """把不规则的发布时间字符串解析成可排序的 (年, 月, 日)。

    兼容 "2026-04-28" / "2026-04" / "2026" / "2024-2025"(年份区间取较晚) 等格式。
    解析不出时返回 (0,0,0) 排到最后。
    """
    s = (s or "").strip()
    years = _PUB_YEAR_RE.findall(s)
    if not years:
        return (0, 0, 0)
    if len(years) >= 2:  # 年份区间(如 2024-2025) → 取较晚的年
        return (max(int(y) for y in years), 0, 0)
    y = int(years[0])
    rest = s.split(years[0], 1)[1]
    nums = re.findall(r"\d{1,2}", rest)
    mo = int(nums[0]) if nums and 1 <= int(nums[0]) <= 12 else 0
    d = int(nums[1]) if len(nums) >= 2 and 1 <= int(nums[1]) <= 31 else 0
    return (y, mo, d)


def _sort_by_published(items: list[dict]) -> list[dict]:
    """按发布时间倒序（最近在前）。稳定排序，时间相同保持原有相对顺序。"""
    return sorted(items, key=lambda it: _published_key(it.get("published", "")), reverse=True)


def _normalize(items: list[dict]) -> list[dict]:
    out = []
    for a in items:
        url = a.get("url", "")
        title = a.get("title", "")
        tags = [t for t in ([a.get("region"), a.get("aspect")] + (a.get("extra_tags") or [])) if t]
        out.append({
            "id": _item_id(url, title),
            "title": title,
            "source": a.get("source", ""),
            "url": url,
            "published": a.get("published", ""),
            "region": a.get("region", ""),
            "aspect": a.get("aspect", ""),
            "summary": a.get("summary", ""),
            "tags": tags,
        })
    return _sort_by_published(out)


# 内置专题“配方”：id -> (title, intro, items builder)
TOPIC_RECIPES = {
    "space-tug": {
        "title": SPACE_TUG_TITLE,
        "intro": SPACE_TUG_INTRO,
        "items": SPACE_TUG_ITEMS,
        "years": 3,
    },
}


def _topic_path(topic_id: str) -> Path:
    return TOPICS_DIR / f"{topic_id}.json"


# ---------------------------------------------------------------------------
# 翻译落地页：复用 weixin_auto_message/news_pages 的渲染块，
# 在我们自己的页面里展示中文内容，仅在页脚保留原文跳转链接。
# ---------------------------------------------------------------------------
def _is_paper(it: dict) -> bool:
    """学术论文 / 技术报告（多为 PDF）：正文常是目录或排版噪声，应改用 GPT 总结。"""
    u = (it.get("url") or "").lower()
    return (
        u.endswith(".pdf")
        or "viewcontent" in u
        or "elib.dlr" in u
        or "eucass.eu" in u
        or "digitalcommons" in u
    )


_CN_NAV_KW = (
    "订阅", "系统", "情报", "赛道", "决策", "报价", "登录", "注册", "客户端",
    "下载", "版权", "扫码", "二维码", "导航", "频道", "专题", "栏目", "菜单", "首页",
    "返回", "上一篇", "下一篇", "责任编辑", "更多", "推荐阅读", "相关阅读", "热门",
    "供应链信息", "市场与", "一键生成", "资讯", "书签", "头条", "简读", "政务",
)

# 出现即整段丢弃的强噪声（站点导航条 / 书签提示等）
_CN_JUNK_SUBSTR = ("设为书签", "将本页面保存", "Ctrl+D", "APP头条", "头条APP")


def _clean_cn_body(text: str) -> str:
    """国内文章正文清洗：去掉站名 / logo / 导航菜单 / 面包屑等非正文段，仅保留正文。

    策略（按段落 \\n\\n 切分）：
    - 先剥离段内图片 markdown，纯图片段直接丢弃；
    - 含句末标点（。！？）的较长段视为正文，保留；
    - 否则若是『大量短词空格罗列』或『命中≥2 个导航关键词』或过短，判为导航/噪声，丢弃。
    """
    if not text:
        return ""
    out: list[str] = []
    for raw in re.split(r"\n{2,}", text):
        p = raw.strip()
        if not p:
            continue
        # 去掉图片 markdown 与 "!Image 3" 之类残留
        p = re.sub(r"!?\[[^\]]*\]\([^)]*\)", "", p)
        p = re.sub(r"!?Image\s*\d*", "", p).strip()
        if not p:
            continue
        if any(j in p for j in _CN_JUNK_SUBSTR):
            continue
        has_end = any(c in p for c in "。！？")
        tokens = p.split()
        avg = sum(len(t) for t in tokens) / max(1, len(tokens))
        many_short = len(tokens) >= 4 and avg <= 8
        nav_hits = sum(1 for kw in _CN_NAV_KW if kw in p)
        if not has_end and (many_short or nav_hits >= 1 or len(p) < 25):
            continue
        out.append(p)
    return "\n\n".join(out).strip()


def _enrich_items(items: list[dict]) -> None:
    """抓取原文 → 清洗正文与图片 → 国外全文翻译/国内用原文，写回 body_zh / images / image。

    复用 weixin_auto_message/news_pages 的抓取、清洗、翻译能力：
    - _http_get：带 UA/Referer 的抓取（含重试）
    - _extract_main_html：提取正文段落 + 文中图片（已过滤订阅/广告段）
    - _batch_translate：分批带标记的稳健中译
    - _proxy_image / _strip_author_bio：图片防盗链代理 / 去作者署名段
    抓取失败（429/403/PDF 等）则回退到策展摘要，保证不空。
    """
    ensure_wam_importable()
    from src import news_pages as np  # noqa: E402
    try:
        from src import topic_ingest  # noqa: E402
        ingest_map = topic_ingest.load_map()
    except Exception:
        ingest_map = {}

    # 1) 优先用海外 GitHub Actions 抓回的全文（绕开国内直连拦截）；否则国内直连抓取
    for it in items:
        url = it.get("url", "")
        text, imgs, og = "", [], None

        ing = ingest_map.get(url)
        if ing and (ing.get("text") or ing.get("content_html")):
            text = (ing.get("text") or "").strip()
            if not text and ing.get("content_html"):
                text, ci, og = np._extract_main_html(ing["content_html"], url)
                imgs = ci or []
            imgs = (imgs or []) + list(ing.get("images") or [])
            og = og or (imgs[0] if imgs else None)

        if not text:
            try:
                r = np._http_get(url)
                ctype = (r.headers.get("Content-Type") or "").lower()
                head = (r.text or "")[:2000].lower()
                if "html" in ctype or "<html" in head or "<article" in head or "<body" in head:
                    text, imgs, og = np._extract_main_html(r.text, url)
            except Exception as e:
                log.info("topic fetch failed %s: %s", url, e)

        it["_text"] = text
        it["_imgs"] = imgs or []
        it["_og"] = og

    # 2) 国外**非论文**：把抓到正文的篇目分批翻译（论文走 GPT 总结，不逐句翻译）
    en_idx = [
        i for i, it in enumerate(items)
        if it.get("region") == "国外" and it.get("_text") and not _is_paper(it)
    ]
    if en_idx:
        en_blocks = [items[i]["_text"] for i in en_idx]
        log.info("topic translate %d foreign articles", len(en_blocks))
        zh_blocks = np._batch_translate(en_blocks)
        for i, zh in zip(en_idx, zh_blocks):
            items[i]["_zh"] = zh

    # 2b) 论文 / 技术报告：用 GPT 总结『做了什么』，避免目录/噪声直接落正文
    try:
        from src import summarizer  # noqa: E402
    except Exception:
        summarizer = None
    for it in items:
        if not _is_paper(it):
            continue
        if summarizer is None:
            it["_zh"] = it.get("summary", "")
            continue
        try:
            it["_paper_zh"] = summarizer.summarize_paper(
                it.get("title", ""), raw_text=it.get("_text", ""), hint=it.get("summary", ""),
            )
        except Exception as e:
            log.warning("summarize paper failed %s: %s", it.get("url"), e)
            it["_paper_zh"] = it.get("summary", "")

    # 3) 组装 body_zh / 图片
    for it in items:
        text = it.get("_text") or ""
        if _is_paper(it):
            body = it.get("_paper_zh") or it.get("summary", "")
        elif it.get("region") == "国内":
            # URL 指向站点首页（无有效文章路径）时，抓到的多为首页营销/导航，
            # 与正文无关，直接用策展摘要兜底。
            from urllib.parse import urlparse as _urlparse
            _path = _urlparse(it.get("url", "")).path.strip("/")
            if not _path:
                body = it.get("summary", "")
            else:
                body = _clean_cn_body(text) if text else ""
                if not body.strip():
                    body = it.get("summary", "")
        else:
            body = it.get("_zh") or ""
            body = np._strip_author_bio(body) if body else (it.get("summary", ""))
        it["body_zh"] = (body or "").strip()

        imgs = []
        for u in (it.get("_imgs") or [])[:6]:
            pu = np._proxy_image(u)
            if pu and pu not in imgs:
                imgs.append(pu)
        hero_raw = it.get("_og") or ((it.get("_imgs") or [None])[0])
        it["image"] = np._proxy_image(hero_raw) if hero_raw else ""
        it["image_raw"] = hero_raw or ""   # 原始图 URL，供推送卡片走本机 /img 代理
        it["images"] = imgs

        for k in ("_text", "_imgs", "_og", "_zh", "_paper_zh"):
            it.pop(k, None)


def _render_item_page(topic_id: str, item: dict) -> str:
    """把单条专题条目渲染成一张中文落地页 HTML（含正文与文中图片，仅页脚保留原文链接）。"""
    ensure_wam_importable()
    import html as _html
    from src import news_pages as np  # noqa: E402

    body_zh = item.get("body_zh") or item.get("summary") or ""
    paras = [p.strip() for p in re.split(r"\n{2,}", body_zh) if p.strip()]
    imgs = item.get("images") or []
    hero = item.get("image") or ""

    parts: list[str] = []
    img_i = 0
    # 文中图片：每隔约 3 段插一张，剩余的补在结尾
    for k, p in enumerate(paras):
        parts.append(f"<p>{_html.escape(p)}</p>")
        if imgs and (k + 1) % 3 == 0 and img_i < len(imgs):
            if imgs[img_i] != hero:
                parts.append(f'<img class="hero" src="{_html.escape(imgs[img_i])}" alt="">')
            img_i += 1
    while img_i < len(imgs):
        if imgs[img_i] != hero:
            parts.append(f'<img class="hero" src="{_html.escape(imgs[img_i])}" alt="">')
        img_i += 1

    body_html = "\n".join(parts) or (
        '<div style="background:#fff7e6;border:1px solid #ffd591;border-radius:6px;'
        'padding:14px 16px;color:#8c4a00;">暂无正文，请点击下方原文链接查看。</div>'
    )
    tags = item.get("tags") or []
    tags_html = (
        '<div class="tags">' + "".join(f"<span>#{_html.escape(t)}</span>" for t in tags) + "</div>"
        if tags else ""
    )
    hero_html = f'<img class="hero" src="{_html.escape(hero)}" alt="">' if hero else ""
    return np._PAGE_TPL.format(
        title_zh=_html.escape(item.get("title", "")),
        source=_html.escape(item.get("source", "")),
        published=_html.escape(item.get("published", "")),
        tags_html=tags_html,
        hero_html=hero_html,
        body_html=body_html,
        orig_url=_html.escape(item.get("url", "")),
    )


def _generate_pages(topic_id: str, items: list[dict]) -> None:
    """为每条目生成持久落地页，并把 page_url 写回 item。"""
    out_dir = TOPIC_PAGES_DIR / topic_id
    out_dir.mkdir(parents=True, exist_ok=True)
    base = _public_base()
    for it in items:
        try:
            (out_dir / f"{it['id']}.html").write_text(
                _render_item_page(topic_id, it), encoding="utf-8"
            )
            it["page_url"] = f"{base}/t/{topic_id}/{it['id']}"
        except Exception as e:
            log.warning("render topic page failed %s: %s", it.get("id"), e)
            it["page_url"] = ""


def topic_page_file(topic_id: str, page_id: str) -> Path:
    return TOPIC_PAGES_DIR / topic_id / f"{page_id}.html"


def _build_topic(topic_id: str) -> dict:
    recipe = TOPIC_RECIPES.get(topic_id)
    if not recipe:
        raise ValueError(f"未知专题：{topic_id}（暂仅支持内置配方）")
    items = _normalize(recipe["items"])
    domestic = sum(1 for it in items if it["region"] == "国内")
    intl = sum(1 for it in items if it["region"] == "国外")
    return {
        "id": topic_id,
        "title": recipe["title"],
        "intro": recipe["intro"],
        "years": recipe.get("years", 3),
        "updated_at": _now_iso(),
        "stats": {
            "count": len(items),
            "domestic": domestic,
            "intl": intl,
            "design": sum(1 for it in items if it["aspect"] == "总体设计"),
            "tech": sum(1 for it in items if it["aspect"] == "专业技术"),
        },
        "items": items,
    }


def refresh(topic_id: str = "space-tug") -> dict:
    """（重新）生成专题数据并落库，保留既有推送状态。"""
    topic = _build_topic(topic_id)
    _enrich_items(topic["items"])
    _generate_pages(topic_id, topic["items"])
    old = _read(topic_id)
    if old:
        topic["created_at"] = old.get("created_at") or topic["updated_at"]
        topic["pushed"] = old.get("pushed") or _empty_pushed()
    else:
        topic["created_at"] = topic["updated_at"]
        topic["pushed"] = _empty_pushed()
    _topic_path(topic_id).write_text(
        json.dumps(topic, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return topic


def _empty_pushed() -> dict:
    return {"admin": False, "admin_at": None, "all": False, "all_at": None}


def _read(topic_id: str) -> dict | None:
    p = _topic_path(topic_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def get_topic(topic_id: str) -> dict | None:
    topic = _read(topic_id)
    if topic is None and topic_id in TOPIC_RECIPES:
        # 首次访问内置专题：自动生成
        topic = refresh(topic_id)
    elif topic and any(not it.get("page_url") or "body_zh" not in it for it in topic.get("items") or []):
        # 旧数据缺少落地页/全文：整体重建一次
        topic = refresh(topic_id)
    if topic and isinstance(topic.get("items"), list):
        # 兜底：已存盘旧数据也按发布时间倒序返回（最近在前）
        topic["items"] = _sort_by_published(topic["items"])
    return topic


def get_item(topic_id: str, item_id: str) -> dict | None:
    topic = get_topic(topic_id)
    if not topic:
        return None
    for it in topic.get("items") or []:
        if it.get("id") == item_id:
            return it
    return None


def list_topics() -> list[dict]:
    out = []
    seen = set()
    for p in sorted(TOPICS_DIR.glob("*.json")):
        try:
            t = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        seen.add(t["id"])
        out.append(_summary(t))
    # 内置但还没生成的也列出来
    for tid, recipe in TOPIC_RECIPES.items():
        if tid not in seen:
            out.append({
                "id": tid, "title": recipe["title"], "intro": recipe["intro"],
                "count": len(recipe["items"]), "updated_at": "",
                "pushed": _empty_pushed(), "years": recipe.get("years", 3),
            })
    out.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return out


def _summary(t: dict) -> dict:
    return {
        "id": t["id"],
        "title": t["title"],
        "intro": t.get("intro", ""),
        "count": (t.get("stats") or {}).get("count") or len(t.get("items") or []),
        "updated_at": t.get("updated_at", ""),
        "pushed": t.get("pushed") or _empty_pushed(),
        "years": t.get("years", 3),
        "stats": t.get("stats") or {},
    }


# ---------------------------------------------------------------------------
# 企业微信推送（先私推管理员，确认后全员）
# ---------------------------------------------------------------------------
CARD_LIMIT = 8  # 企业微信 news 单条消息最多 8 张卡片


def _overview_text(topic: dict) -> str:
    st = topic.get("stats") or {}
    return (
        f"🛰️ 专题情报 · {topic['title']}\n\n"
        f"{topic.get('intro', '')}\n\n"
        f"共 {st.get('count', 0)} 篇（国外 {st.get('intl', 0)} / 国内 {st.get('domestic', 0)}；"
        f"总体设计 {st.get('design', 0)} / 专业技术 {st.get('tech', 0)}）· 近 {topic.get('years', 3)} 年\n"
        f"下面按条目逐篇推送，点击卡片可阅读原文。"
    )


def _build_cards(topic: dict) -> list[dict]:
    """把专题条目转成企业微信 news 图文卡片（微信插件可见、可点开原文）。

    卡片缩略图走本机 /img 代理（先 prefetch 预热，抓得到才塞 picurl，避免灰框）。
    """
    ensure_wam_importable()
    try:
        from src import img_proxy  # noqa: E402
    except Exception:
        img_proxy = None

    cards: list[dict] = []
    items = topic.get("items") or []
    # 先国外后国内，保持与小程序一致的阅读顺序
    ordered = [it for it in items if it["region"] == "国外"] + \
              [it for it in items if it["region"] == "国内"]
    for it in ordered:
        prefix = f"【{it.get('region', '')}·{it.get('aspect', '')}】"
        # 国内新闻直接跳原文网页；国外仍走我们自己的中文落地页
        if it.get("region") == "国内":
            link = it.get("url") or it.get("page_url") or ""
        else:
            link = it.get("page_url") or it.get("url") or ""
        card = {
            "title": (prefix + (it.get("title") or "")).strip()[:120],
            "description": (it.get("summary") or "")[:500],
            "url": link,
        }
        raw = it.get("image_raw") or ""
        if img_proxy and raw:
            ref = it.get("url") or ""
            try:
                if img_proxy.prefetch(raw, ref):
                    card["picurl"] = img_proxy.proxify(raw, ref)
            except Exception:
                pass
        cards.append(card)
    return cards


def push(topic_id: str, scope: str) -> dict:
    """scope: 'admin' 私推管理员 / 'all' 全员推送。

    采用与每日推送一致的「图文条目卡片」格式（send_news），微信插件中可见、可点开原文。
    先发一条总览文字，再按每条 8 张分多条图文消息。
    """
    topic = get_topic(topic_id)
    if not topic:
        raise ValueError("专题不存在，请先生成")
    if scope not in ("admin", "all"):
        raise ValueError("scope 只能是 admin 或 all")

    ensure_wam_importable()
    import time
    from src import wecom  # noqa: E402

    to_user = ADMIN_PUSH_USER if scope == "admin" else ALL_PUSH_USER

    results: list[dict] = []
    # 1) 总览文字
    results.extend(wecom.send_text(_overview_text(topic), to_user=to_user) or [])

    # 2) 图文卡片，按 8 张一条分发
    cards = [c for c in _build_cards(topic) if c.get("url")]
    for i in range(0, len(cards), CARD_LIMIT):
        time.sleep(2)
        chunk = cards[i:i + CARD_LIMIT]
        res = wecom.send_news(chunk, to_user=to_user)
        if res is not None:
            results.append(res)

    ok = bool(results) and all((r or {}).get("errcode", 0) == 0 for r in results)
    pushed = topic.get("pushed") or _empty_pushed()
    if ok:
        pushed[scope] = True
        pushed[f"{scope}_at"] = _now_iso()
        topic["pushed"] = pushed
        _topic_path(topic_id).write_text(
            json.dumps(topic, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return {"ok": ok, "scope": scope, "to_user": to_user, "parts": len(results), "results": results}
