"""设置面板的数据源：把 .env 里的配置整理成表单，再安全地写回去。

面板显示什么、怎么校验、要不要重启，全在这里描述：
- `schema()`   给网页画表格用（分组 / 字段 / 选项 / 提示）
- `snapshot()` 当前实际生效的值（读的是内存里的 settings，不是 .env 原文）
- `apply()`    校验 → 写 .env → 同步到内存里的 settings（多数设置立刻生效）
- `persona_*`  人格文件的读写

server.py 只负责把这些接成 HTTP，web/index.html 只负责画。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import requests

from .config import DATA_DIR, ROOT, Settings, settings

ENV_FILE = ROOT / ".env"
BLOB_BEGIN = "# >>> 设置面板补充（自动生成，别手改）"
BLOB_END = "# <<< 设置面板补充"

# 改这些要重新「找脑子」：在线 / Ollama / 本地模型之间可能换了一家
MODEL_KEYS = {
    "FISH_PROVIDER", "API_BASE", "API_KEY", "API_MODEL",
    "OLLAMA_BASE", "OLLAMA_MODEL", "HF_MODEL_DIR",
}
# 改这些要重置语音引擎的「坏过一次就别再试」标记
VOICE_KEYS = {
    "FISH_TTS", "BREEZE_API_KEY", "BREEZE_API_BASE", "BREEZE_VOICE_ID",
    "BREEZE_MODEL_ID", "BREEZE_INSTRUCTIONS", "BREEZE_GUIDANCE",
    "BREEZE_SPEED", "BREEZE_PITCH", "BREEZE_NORMALIZE",
    "BREEZE_TARGET_DBFS", "FISH_STT_MODEL",
}


# ---------------------------------------------------------------- 字段定义
@dataclass(frozen=True)
class Field:
    env: str
    attr: str            # 对应 Settings 上的字段名
    label: str
    group: str
    kind: str = "text"   # text | secret | number | slider | bool | select | textarea | time
    help: str = ""
    placeholder: str = ""
    options: tuple[tuple[str, str], ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    restart: bool = False
    advanced: bool = False


GROUPS: list[dict] = [
    {"id": "overview", "icon": "🏠", "title": "概览",
     "desc": "她现在是什么状态。下面的地址和端口改完要重启大肥鱼。"},
    {"id": "model", "icon": "🧠", "title": "模型",
     "desc": "她用哪个脑子想事情。换模型、换服务商都在这里，存完立刻生效。"},
    {"id": "qq", "icon": "💬", "title": "QQ / 微信",
     "desc": "她说的话往哪儿发。QQ 走 NapCat（OneBot v11），登录在 NapCat 那边扫码。"},
    {"id": "sister", "icon": "🐠", "title": "妹妹",
     "desc": "给鲸鲸养个妹妹：另一个 QQ 号、另一份记忆。开了之后她俩会自己聊，"
             "但要留刹车——两个接了模型的号能聊到天亮。"},
    {"id": "voice", "icon": "🔊", "title": "语音",
     "desc": "她怎么说话、怎么听话：TTS 引擎、音色、语气，还有语音条和表情包的频率。"},
    {"id": "persona", "icon": "🐟", "title": "人格",
     "desc": "她是谁、什么脾气。私聊、群聊、网页可以各用一份。"},
    {"id": "reply", "icon": "🗣️", "title": "回复",
     "desc": "聊多少、记多少、什么时候自己找你说话。"},
]

EDGE_VOICES = (
    ("zh-CN-XiaoyiNeural", "晓艺 · 活泼（现在用的）"),
    ("zh-CN-XiaoxiaoNeural", "晓晓 · 温柔"),
    ("zh-CN-XiaochenNeural", "晓辰 · 慵懒"),
    ("zh-CN-XiaohanNeural", "晓涵 · 知性"),
    ("zh-CN-XiaomengNeural", "晓梦 · 甜"),
    ("zh-CN-XiaomoNeural", "晓墨 · 沉"),
    ("zh-CN-XiaoruiNeural", "晓睿 · 稳"),
    ("zh-CN-XiaoshuangNeural", "晓双 · 小孩"),
    ("zh-CN-XiaoyanNeural", "晓颜 · 软"),
    ("zh-CN-XiaoyouNeural", "晓悠 · 童声"),
    ("zh-CN-XiaozhenNeural", "晓甄 · 清"),
    ("zh-CN-YunxiNeural", "云希 · 少年（男）"),
    ("zh-CN-YunjianNeural", "云健 · 男声"),
    ("zh-TW-HsiaoYuNeural", "曉雨 · 台腔软"),
    ("zh-TW-HsiaoChenNeural", "曉臻 · 台腔稳"),
    ("zh-HK-HiuMaanNeural", "曉曼 · 粤语"),
)

PROVIDERS = (
    ("auto", "自动（先本地，再 Ollama，最后在线）"),
    ("api", "在线 API（OpenAI 兼容）"),
    ("ollama", "本地 Ollama"),
    ("hf", "本地 transformers 权重"),
)

TTS_ENGINES = (
    ("breeze", "BreezeBlue（云端，按字符计费）"),
    ("edge", "edge-tts（微软在线，免费）"),
)

STT_MODELS = (
    ("tiny", "tiny · 最快（约 75MB）"),
    ("base", "base · 快（约 145MB）"),
    ("small", "small · 平衡（约 480MB，现在用的）"),
    ("medium", "medium · 准（约 1.5GB）"),
    ("large-v3", "large-v3 · 最准（约 3GB）"),
)

FIELDS: list[Field] = [
    # ---------- 概览 / 服务 ----------
    Field("FISH_HOST", "host", "监听地址", "overview", "text", restart=True,
          help="只给自己用就留 127.0.0.1；要让局域网别的设备打开，填 0.0.0.0。"),
    Field("FISH_PORT", "port", "端口", "overview", "number", restart=True,
          minimum=1024, maximum=65535, step=1, help="浏览器打开 http://地址:端口。"),

    # ---------- 模型 ----------
    Field("FISH_PROVIDER", "provider", "用哪种脑子", "model", "select", options=PROVIDERS,
          help="auto 会把本地、Ollama、在线 API 依次试一遍，哪个通用哪个。"),
    Field("API_BASE", "api_base", "API 地址", "model", "text",
          placeholder="https://api.deepseek.com/v1",
          help="OpenAI 兼容接口，一般以 /v1 结尾。"),
    Field("API_MODEL", "api_model", "模型名", "model", "text",
          placeholder="deepseek-chat"),
    Field("API_KEY", "api_key", "API Key", "model", "secret",
          placeholder="sk-…", help="只存在本机的 .env 里，不会上传。"),
    Field("FISH_TEMPERATURE", "temperature", "跳脱程度", "model", "slider",
          minimum=0.1, maximum=1.5, step=0.05,
          help="越高越像人、越爱自己发挥；越低越稳、越像复读机。0.95 是现在的手感。"),
    Field("OLLAMA_BASE", "ollama_base", "Ollama 地址", "model", "text", advanced=True,
          placeholder="http://127.0.0.1:11434/v1"),
    Field("OLLAMA_MODEL", "ollama_model", "Ollama 模型", "model", "text", advanced=True,
          placeholder="qwen2.5:7b-instruct"),
    Field("HF_MODEL_DIR", "hf_model_dir", "本地权重目录", "model", "text", advanced=True,
          placeholder="留空 = 自动找 models/ 下第一个"),

    # ---------- QQ / 微信 ----------
    Field("QQ_API", "qq_api", "NapCat 地址", "qq", "text",
          placeholder="http://127.0.0.1:3000",
          help="NapCat 里开的 HTTP 服务端地址，发消息走这里。"),
    Field("QQ_TOKEN", "qq_token", "NapCat Token", "qq", "secret",
          help="NapCat 里设了 token 才填；没设就空着。"),
    Field("NAPCAT_DIR", "napcat_dir", "NapCat 目录", "qq", "text", advanced=True,
          placeholder="D:\\NapCat",
          help="「启动 NapCat」按钮去这里找 startNapCat.ps1；留空自动找 D:\\NapCat、C:\\NapCat。"),
    Field("FISH_GROUP_AT_ONLY", "group_at_only", "群里只认 @", "qq", "bool",
          help="关掉之后群里每条消息都会回——不建议，容易被踢。"),
    Field("FISH_GROUP_CHAT", "group_chat", "没被 @ 也接话", "qq", "bool",
          help="她觉得该接就接一句，不带硬性刷屏。"),
    Field("FISH_GROUP_CHIME", "group_chime", "群里自己冒泡", "qq", "bool",
          help="群里正聊着的时候自己插一句；关掉只保留「接话」。"),
    Field("FISH_GROUP_ALLOW", "group_allow", "只在哪些群", "qq", "text",
          placeholder="235069995,123456（留空 = 所有群）"),
    Field("FISH_GROUP_MAX_HOUR", "group_max_hour", "每群每小时", "qq", "number",
          minimum=1, maximum=60, step=1, help="单位：句。"),
    Field("FISH_GROUP_GAP", "group_gap", "两次至少隔", "qq", "number",
          minimum=0, maximum=120, step=0.5, help="单位：分钟。"),
    Field("FISH_GROUP_MAX_DAY", "group_max_day", "每天最多", "qq", "number",
          minimum=1, maximum=200, step=1, help="单位：句。"),
    Field("FISH_GROUP_RATE", "group_rate", "刷屏时每秒最多看", "qq", "number",
          minimum=0.5, maximum=20, step=0.5, advanced=True,
          help="单位：条。防止刷屏群把模型打爆。"),
    Field("WECHAT_WEBHOOK", "wechat_webhook", "微信回发地址", "qq", "text", advanced=True,
          placeholder="http://你的桥/reply", help="接第三方微信桥时用；不接就空着。"),

    # ---------- 妹妹 ----------
    Field("FISH_SISTER", "sister", "养个妹妹", "sister", "bool",
          help="再养一条鱼：另一个 QQ 号、另一份记忆，数据在 sister/。打开之后才会管她。"),
    Field("FISH_SISTER_UIN", "sister_uin", "妹妹的 QQ 号", "sister", "text",
          placeholder="妹妹那个小号",
          help="她得先加鲸鲸好友（互相加）。留空就自动问妹妹的 NapCat。"),
    Field("FISH_SELF_UIN", "self_uin", "鲸鲸的 QQ 号", "sister", "text",
          placeholder="留空 = 自动问 NapCat",
          help="妹妹得知道谁是姐姐，才知道该回谁、该防谁。"),
    Field("FISH_OWNER_UIN", "owner_uin", "主人 QQ 号", "sister", "text",
          placeholder="你自己的 QQ",
          help="妹妹默认只回姐姐；填了这里，你发她也回。陌生人仍然不理，免得小号被举报。"),
    Field("FISH_PEER_NAME", "peer_name", "记忆里管对面叫", "sister", "text", advanced=True,
          placeholder="妹妹 / 姐姐",
          help="记长期记忆时用这个称呼。不填一律记成「主人说」，两个人的话会混成一个人。"),
    Field("FISH_SISTER_API", "sister_api", "妹妹的 NapCat 地址", "sister", "text",
          placeholder="http://127.0.0.1:3001",
          help="第二份 NapCat 的 HTTP 端口，别跟鲸鲸的 3000 撞。"),
    Field("FISH_SISTER_TOKEN", "sister_token", "妹妹的 NapCat Token", "sister", "secret"),
    Field("FISH_SISTER_PORT", "sister_port", "妹妹的网页端口", "sister", "number",
          minimum=1024, maximum=65535, step=1,
          help="妹妹自己的面板：http://127.0.0.1:端口。填完要重启妹妹才生效。"),
    Field("FISH_SISTER_NAPCAT", "sister_napcat", "妹妹的 NapCat 目录", "sister", "text",
          advanced=True, placeholder="D:\\NapCat2",
          help="第二份 NapCat 装在哪。别填鲸鲸那份：那份目录里的启动脚本会先关掉本机所有 QQ。"
               "留空就在鲸鲸那份旁边找 NapCat2。"),
    Field("FISH_PEER_OPEN", "peer_open", "让鲸鲸主动找妹妹", "sister", "bool",
          help="隔一阵她自己想起来找妹妹说话；妹妹只回、不缠人。"),
    Field("FISH_PEER_OPEN_GAP", "peer_open_gap", "至少隔几小时", "sister", "number",
          minimum=0.5, maximum=72, step=0.5),
    Field("FISH_PEER_OPEN_DAY", "peer_open_day", "一天最多找几次", "sister", "number",
          minimum=1, maximum=20, step=1),
    Field("FISH_PEER_MAX", "peer_max", "一轮最多回几句", "sister", "number",
          minimum=1, maximum=50, step=1,
          help="两边各自算。到顶两边都停，免得两个号聊到天亮。"),
    Field("FISH_PEER_COOLDOWN", "peer_cooldown", "聊够了歇几分钟", "sister", "number",
          minimum=5, maximum=1440, step=5),
    Field("FISH_PEER_DAY", "peer_day", "一天最多几句", "sister", "number",
          minimum=1, maximum=1000, step=10, help="两边各自算。"),
    Field("FISH_PEER_GAP", "peer_gap", "两次之间至少隔几秒", "sister", "number",
          minimum=1, maximum=120, step=1, advanced=True),
    Field("FISH_PEER_QUIET", "peer_quiet", "她俩的免打扰", "sister", "text",
          placeholder="23-9", advanced=True, help="这个时段鲸鲸不会主动找妹妹。"),

    # ---------- 语音 ----------
    Field("FISH_TTS", "tts_provider", "语音引擎", "voice", "select", options=TTS_ENGINES,
          help="Breeze 连不上 / 没填 key 会自动退回 edge-tts，再退回 Windows 自带语音。"),
    Field("BREEZE_API_KEY", "breeze_api_key", "Breeze Key", "voice", "secret",
          placeholder="brz_…", help="在 breezeblue.ai 的 API keys 页面建一个。"),
    Field("BREEZE_VOICE_ID", "breeze_voice_id", "Breeze 音色 ID", "voice", "text",
          placeholder="voc_8yud7hz7c9q2"),
    Field("BREEZE_INSTRUCTIONS", "breeze_instructions", "表演指令", "voice", "textarea",
          placeholder="声音低一点、软一点…",
          help="只影响这一次的表演，不改音色本身：语气、气声、快慢、要不要尖。"),
    Field("BREEZE_GUIDANCE", "breeze_guidance", "听指令程度", "voice", "slider",
          minimum=1, maximum=10, step=0.5, help="越高越按上面的指令来。"),
    Field("BREEZE_SPEED", "breeze_speed", "语速", "voice", "slider",
          minimum=0.5, maximum=2.0, step=0.05, help="1.0 是原速；调快时音调也会跟着上去。"),
    Field("BREEZE_PITCH", "breeze_pitch", "降调", "voice", "slider",
          minimum=0.5, maximum=1.0, step=0.01,
          help="1.0 = 不处理；0.93 大约低一个半音；越低越沉。"),
    Field("BREEZE_NORMALIZE", "breeze_normalize", "统一响度", "voice", "bool",
          help="把每条语音拉到同一个响度，免得有的语音条小得听不见。"),
    Field("BREEZE_TARGET_DBFS", "breeze_target_dbfs", "目标响度", "voice", "slider",
          minimum=-40, maximum=-5, step=1, advanced=True, help="单位 dBFS，越小越轻。"),
    Field("BREEZE_MODEL_ID", "breeze_model_id", "Breeze 模型", "voice", "text", advanced=True,
          placeholder="breeze-tts-2"),
    Field("BREEZE_API_BASE", "breeze_api_base", "Breeze 接口", "voice", "text", advanced=True,
          placeholder="https://api.breeze.blue/v1"),
    Field("FISH_VOICE", "voice", "edge 音色", "voice", "select", options=EDGE_VOICES,
          help="国内连不上 edge 时会自动换成 Windows 自带语音，那个改不了音色。"),
    Field("FISH_VOICE_RATE", "voice_rate", "语速", "voice", "text",
          placeholder="+6%", help="略快一点更精神，带 %。"),
    Field("FISH_VOICE_PITCH", "voice_pitch", "音调", "voice", "text",
          placeholder="+25Hz", help="往上加更像小孩，+40Hz 更奶，带 Hz。"),
    Field("FISH_STT_MODEL", "stt_model", "听写模型", "voice", "select", options=STT_MODELS,
          help="第一次用会下载；换大会慢一点，但听得准。"),
    Field("FISH_VOICE_REPLY", "voice_reply", "QQ 语音条", "voice", "bool",
          help="她可以真的发语音条；关掉就只打字。"),
    Field("FISH_STICKER_REPLY", "sticker_reply", "QQ 表情包", "voice", "bool"),
    Field("FISH_VOICE_MAX", "voice_max", "语音每天最多", "voice", "number",
          minimum=0, maximum=50, step=1, help="单位：条。群里自动减半。"),
    Field("FISH_VOICE_GAP", "voice_gap", "语音两次至少隔", "voice", "number",
          minimum=0, maximum=3600, step=5, help="单位：秒。"),
    Field("FISH_STICKER_MAX", "sticker_max", "表情每天最多", "voice", "number",
          minimum=0, maximum=50, step=1),
    Field("FISH_STICKER_GAP", "sticker_gap", "表情两次至少隔", "voice", "number",
          minimum=0, maximum=3600, step=5, help="单位：秒。"),
    Field("FISH_STICKER_DIR", "sticker_dir", "表情包目录", "voice", "text", advanced=True,
          placeholder="留空 = 项目下的 stickers/"),

    # ---------- 回复 / 记忆 / 主动 ----------
    Field("FISH_MEMORY_WINDOW", "memory_window", "记得最近几轮", "reply", "number",
          minimum=1, maximum=100, step=1, help="单位：轮（一问一答算一轮）。"),
    Field("FISH_SUMMARY_AFTER", "summary_after", "攒多少条压成笔记", "reply", "number",
          minimum=4, maximum=200, step=1),
    Field("FISH_RECALL_K", "recall_k", "每次想起几件事", "reply", "number",
          minimum=0, maximum=10, step=1),
    Field("FISH_MEMORY_STREAM", "memory_stream", "会忘的记忆", "reply", "bool",
          help="关掉只剩摘要 + 固定事实，她就不会慢慢忘事了。"),
    Field("FISH_TIDY_HOURS", "tidy_hours", "多久整理一次", "reply", "number",
          minimum=0.2, maximum=72, step=0.5, help="单位：小时。记模糊 → 合并 → 遗忘。"),
    Field("FISH_SELF_EDIT", "self_edit", "她自己会变", "reply", "bool",
          help="人格文件末尾有一小段是她自己的：隔一阵子照着最近的经历加一条、改一条或删一条；"
               "关掉就一直是现在这样。"),
    Field("FISH_SELF_EDIT_HOURS", "self_edit_hours", "多久想一次自己", "reply", "number",
          minimum=1, maximum=720, step=1,
          help="单位：小时。到点也要攒够新事才真改，不是每次都想。"),
    Field("FISH_SELF_EDIT_MIN", "self_edit_min", "攒够几件事才想", "reply", "number",
          minimum=1, maximum=50, step=1, advanced=True,
          help="单位：条聊天。太少了容易为一句气话改自己。"),
    Field("FISH_WEBSEARCH", "websearch", "会上网看一眼", "reply", "bool",
          help="问到今天 / 比分 / 转会这类会变的事，先搜一眼再说。"),
    Field("FISH_CITY", "sky_city", "她在的城市", "reply", "text",
          placeholder="上海", help="填了她就知道天黑没黑、什么月亮、下没下雨。"),
    Field("FISH_LAT", "sky_lat", "纬度", "reply", "number",
          minimum=-90, maximum=90, step=0.01, advanced=True,
          help="填了城市就不用填；两个都填以坐标为准。"),
    Field("FISH_LON", "sky_lon", "经度", "reply", "number",
          minimum=-180, maximum=180, step=0.01, advanced=True),
    Field("FISH_PROACTIVE", "proactive", "会自己找你", "reply", "bool",
          help="关掉之后她只在你说话时回。"),
    Field("FISH_PROACTIVE_MAX", "proactive_max", "每天主动几条", "reply", "number",
          minimum=0, maximum=20, step=1, help="补话也算在里面。"),
    Field("FISH_PROACTIVE_GAP", "proactive_gap", "两次主动至少隔", "reply", "number",
          minimum=0, maximum=72, step=0.5, help="单位：小时。"),
    Field("FISH_PROACTIVE_IDLE", "proactive_idle", "你多久没说话才想起你", "reply", "number",
          minimum=0, maximum=72, step=0.5, help="单位：小时。"),
    Field("FISH_FOLLOW", "proactive_follow", "聊到一半会补一句", "reply", "bool"),
    Field("FISH_FOLLOW_IDLE", "proactive_follow_idle", "补话等多久", "reply", "number",
          minimum=1, maximum=240, step=1, help="单位：分钟。"),
    Field("FISH_FOLLOW_WINDOW", "proactive_follow_window", "补话随机窗口", "reply", "number",
          minimum=0, maximum=240, step=1, help="在上面那个时间之后，再随机推迟这么多分钟。"),
    Field("FISH_FOLLOW_MAX", "proactive_follow_max", "每天最多补几句", "reply", "number",
          minimum=0, maximum=10, step=1),
    Field("FISH_QUIET", "proactive_quiet", "免打扰时段", "reply", "text",
          placeholder="23-8", help="格式「开始-结束」，24 小时制，跨午夜就写 23-8。"),
    Field("FISH_SLOTS", "proactive_slots", "早晚定时问候", "reply", "bool",
          help="早安 / 晚安各一条，在窗口里随机挑时刻发。"),
    Field("FISH_MORNING", "proactive_morning", "早安时间", "reply", "time"),
    Field("FISH_NIGHT", "proactive_night", "晚安时间", "reply", "time"),
    Field("FISH_SLOT_WINDOW", "proactive_slot_window", "定时窗口", "reply", "number",
          minimum=5, maximum=240, step=5, help="单位：分钟。8:00 + 60 = 8:00~9:00 之间随机。"),
]

PRESETS: dict[str, list[dict]] = {
    "model": [
        {"label": "DeepSeek", "env": {"API_BASE": "https://api.deepseek.com/v1",
                                      "API_MODEL": "deepseek-chat", "FISH_PROVIDER": "api"}},
        {"label": "Kimi", "env": {"API_BASE": "https://api.moonshot.cn/v1",
                                  "API_MODEL": "kimi-k2-0905-preview", "FISH_PROVIDER": "api"}},
        {"label": "智谱 GLM", "env": {"API_BASE": "https://open.bigmodel.cn/api/paas/v4",
                                      "API_MODEL": "glm-4-flash", "FISH_PROVIDER": "api"}},
        {"label": "阿里百炼", "env": {"API_BASE": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                                      "API_MODEL": "qwen-plus", "FISH_PROVIDER": "api"}},
        {"label": "硅基流动", "env": {"API_BASE": "https://api.siliconflow.cn/v1",
                                      "API_MODEL": "Qwen/Qwen3-8B", "FISH_PROVIDER": "api"}},
        {"label": "OpenRouter", "env": {"API_BASE": "https://openrouter.ai/api/v1",
                                        "API_MODEL": "deepseek/deepseek-chat",
                                        "FISH_PROVIDER": "api"}},
    ],
}

_BY_ENV = {f.env: f for f in FIELDS}


# ---------------------------------------------------------------- 读
def _field_dict(f: Field) -> dict:
    return {
        "env": f.env,
        "label": f.label,
        "kind": f.kind,
        "help": f.help,
        "placeholder": f.placeholder,
        "options": [{"value": v, "label": t} for v, t in f.options],
        "min": f.minimum,
        "max": f.maximum,
        "step": f.step,
        "restart": f.restart,
        "advanced": f.advanced,
        "group": f.group,
    }


def schema() -> list[dict]:
    out = []
    for g in GROUPS:
        out.append({
            **g,
            "presets": PRESETS.get(g["id"], []),
            "fields": [_field_dict(f) for f in FIELDS if f.group == g["id"]],
        })
    return out


def _serialize(f: Field, value):
    if f.kind == "bool":
        return bool(value)
    if f.kind in ("number", "slider"):
        cur = getattr(settings, f.attr, None)
        return int(value) if isinstance(cur, int) else float(value)
    return str(value if value is not None else "")


def read_env_file() -> dict[str, str]:
    """原样读 .env（不信内存）。面板改的是配置，不是运行时算出来的东西。"""
    out: dict[str, str] = {}
    try:
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return out
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, value = s.split("=", 1)
        key = key.strip()
        if key:
            out[key] = value.strip().strip('"').strip("'")
    return out


def snapshot() -> dict:
    """面板要显示的值：.env 里写的为准，没写的才看运行时默认。

    不能直接读运行时的 settings：她的窗会把城市解析成经纬度写回 settings，
    面板要是照抄，一保存就把坐标当成「你填的」写进 .env 了。
    """
    raw = read_env_file()
    defaults = Settings()   # 干净的默认值（同样读 .env），不带运行时改过的东西
    out: dict = {}
    for f in FIELDS:
        if f.env in raw:
            try:
                out[f.env] = _coerce(f, raw[f.env])
                continue
            except ValueError:
                pass
        try:
            out[f.env] = _serialize(f, getattr(defaults, f.attr))
        except Exception:  # noqa: BLE001
            out[f.env] = ""
    return out


# ---------------------------------------------------------------- 校验 / 写
def _coerce(f: Field, raw):
    """把网页传来的值变成 settings 上该有的类型，顺手夹到合法范围。"""
    if f.kind == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "on", "yes", "是")

    if f.kind in ("number", "slider"):
        try:
            val = float(raw)
        except (TypeError, ValueError):
            if raw in ("", None):
                val = 0.0
            else:
                raise ValueError(f"「{f.label}」要填数字")
        if f.minimum is not None:
            val = max(f.minimum, val)
        if f.maximum is not None:
            val = min(f.maximum, val)
        cur = getattr(settings, f.attr, None)
        if isinstance(cur, int) and not isinstance(cur, bool):
            return int(round(val))
        return round(val, 4)

    text = "" if raw is None else str(raw)
    if f.kind == "textarea":
        text = text.replace("\r\n", "\n").strip()
    else:
        text = text.strip()
    if f.kind == "select" and f.options and text:
        allowed = [v for v, _ in f.options]
        if text not in allowed:
            raise ValueError(f"「{f.label}」只能是：{' / '.join(allowed)}")
    return text


def _env_text(f: Field, value) -> str:
    if f.kind == "bool":
        return "1" if value else "0"
    if f.kind in ("number", "slider"):
        return f"{value:g}" if isinstance(value, float) else str(value)
    # .env 是一行一条，多行文本压成一行
    return " ".join(str(value).splitlines()).strip()


_KEY_RE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*)=")


def _strip_blob(lines: list[str]) -> list[str]:
    out, inside = [], False
    for line in lines:
        if line.strip() == BLOB_BEGIN:
            inside = True
            continue
        if line.strip() == BLOB_END:
            inside = False
            continue
        if not inside:
            out.append(line)
    return out


def _blob_values(lines: list[str]) -> dict[str, str]:
    """上一次补在文件末尾小区块里的键值。

    `write_env` 每次都把那个区块整个拆掉重写；不先把旧内容捞出来的话，
    「上一次保存补进去的键」会在这回保存时凭空消失（妹妹的 QQ 号就这么丢过）。
    """
    out: dict[str, str] = {}
    inside = False
    for line in lines:
        s = line.strip()
        if s == BLOB_BEGIN:
            inside = True
            continue
        if s == BLOB_END:
            inside = False
            continue
        if not inside or not s or s.startswith("#") or "=" not in s:
            continue
        key, value = s.split("=", 1)
        key = key.strip()
        if key:
            out[key] = value.strip()
    return out


def write_env(values: dict[str, str]) -> None:
    """只改值、保留注释和顺序；.env 里没有的键补在文件末尾一个小区块里。"""
    try:
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []

    if ENV_FILE.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(ENV_FILE, DATA_DIR / ".env.bak")
        except Exception:  # noqa: BLE001
            pass

    # 先把旧区块里的键记住，再拆区块：不然没在这次保存里的键会被顺手抹掉
    values = {**_blob_values(lines), **values}
    lines = _strip_blob(lines)
    found: dict[str, int] = {}
    for i, line in enumerate(lines):
        hit = _KEY_RE.match(line)
        if hit:
            found[hit.group(2)] = i

    extra: list[str] = []
    for key, value in values.items():
        if key in found:
            lines[found[key]] = f"{key}={value}"
        else:
            extra.append(f"{key}={value}")

    if extra:
        while lines and not lines[-1].strip():
            lines.pop()
        lines += ["", BLOB_BEGIN, *extra, BLOB_END]

    text = "\n".join(lines).rstrip("\n") + "\n"
    ENV_FILE.write_text(text, encoding="utf-8")


def apply(values: dict) -> dict:
    """校验 → 落到内存 → 写回 .env。返回改了哪些、哪些要重启。"""
    unknown = [k for k in values if k not in _BY_ENV]
    if unknown:
        raise ValueError(f"不认识的设置项：{', '.join(unknown)}")

    # 先全部校验完再落地，免得填错一个字段、前面几个已经被改掉了
    before = snapshot()
    parsed: list[tuple[Field, object]] = []
    for f in FIELDS:
        if f.env in values:
            parsed.append((f, _coerce(f, values[f.env])))

    changed: list[str] = []
    updates: dict[str, str] = {}
    for f, val in parsed:
        # 没动过的字段就别碰：她的窗会把城市解析成经纬度写回运行时，
        # 照抄一遍等于把坐标抹掉，还会让 .env 每次保存都变。
        if before.get(f.env) == val:
            continue
        setattr(settings, f.attr, val)
        os.environ[f.env] = _env_text(f, val)
        updates[f.env] = _env_text(f, val)
        changed.append(f.env)
        if os.getenv("FISH_DEBUG_SETTINGS"):
            print(f"[prefs] {f.env}: {before.get(f.env)!r} -> {val!r}")

    if updates:
        write_env(updates)

    restart = [f.env for f in FIELDS if f.restart and f.env in changed]
    return {"changed": changed, "restart": restart, "restart_labels": [
        _BY_ENV[k].label for k in restart
    ]}


def temp_settings(overrides: dict) -> Settings:
    """试听用：拿一份临时配置，不动真正在跑的那份。"""
    cfg = Settings()
    for f in FIELDS:
        if f.env in overrides:
            setattr(cfg, f.attr, _coerce(f, overrides[f.env]))
    return cfg


# ---------------------------------------------------------------- 试试通不通
def test_model(values: dict) -> dict:
    """按面板上的值试着说一句话，看看脑子通不通。不改任何配置。"""
    cfg = temp_settings(values or {})
    provider = (cfg.provider or "auto").lower()
    notes: list[str] = []

    def try_api() -> dict | None:
        if not cfg.api_key:
            notes.append("没填 API Key")
            return None
        from openai import OpenAI

        client = OpenAI(base_url=cfg.api_base, api_key=cfg.api_key, timeout=30)
        start = time.time()
        resp = client.chat.completions.create(
            model=cfg.api_model,
            messages=[{"role": "user", "content": "只回两个字：咕噜"}],
            temperature=0.3,
            max_tokens=16,
        )
        ms = int((time.time() - start) * 1000)
        return {"ok": True, "kind": "api", "model": cfg.api_model, "ms": ms,
                "reply": (resp.choices[0].message.content or "").strip()[:60]}

    def try_ollama() -> dict | None:
        start = time.time()
        r = requests.get(cfg.ollama_base.rstrip("/") + "/models", timeout=3)
        if r.status_code != 200:
            notes.append(f"Ollama 返回 {r.status_code}")
            return None
        names = [d.get("id", "") for d in (r.json().get("data") or [])]
        if not names:
            notes.append("Ollama 在跑，但一个模型都没下")
            return None
        want = cfg.ollama_model
        picked = want if any(n == want for n in names) else names[0]
        return {"ok": True, "kind": "ollama", "model": picked,
                "ms": int((time.time() - start) * 1000), "reply": "；".join(names[:4])}

    def try_hf() -> dict | None:
        path = Path(cfg.hf_model_dir) if cfg.hf_model_dir else None
        if path is None:
            base = ROOT / "models"
            dirs = [d for d in sorted(base.iterdir()) if d.is_dir()] if base.is_dir() else []
            path = next((d for d in dirs if (d / "config.json").exists()), None)
        if path is None or not (path / "config.json").exists():
            notes.append("models/ 里没有可用的权重")
            return None
        return {"ok": True, "kind": "hf", "model": path.name, "ms": 0,
                "reply": f"权重在 {path}"}

    order = {
        "api": [try_api, try_ollama],
        "ollama": [try_ollama],
        "hf": [try_hf],
        "auto": [try_hf, try_ollama, try_api],
    }.get(provider, [try_hf, try_ollama, try_api])

    for step in order:
        try:
            result = step()
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{type(exc).__name__}: {str(exc)[:160]}")
            continue
        if result:
            return {**result, "notes": notes}

    return {"ok": False, "kind": provider, "notes": notes,
            "detail": "都没连上：" + "；".join(notes) if notes else "没有可用的脑子"}


def test_qq(values: dict) -> dict:
    """问一句 NapCat：登录的是谁、在不在线。"""
    cfg = temp_settings(values or {})
    base = (cfg.qq_api or "").rstrip("/")
    if not base:
        return {"ok": False, "detail": "还没填 NapCat 地址"}
    headers = {"Authorization": f"Bearer {cfg.qq_token}"} if cfg.qq_token else {}
    start = time.time()
    try:
        info = requests.get(base + "/get_login_info", headers=headers, timeout=3)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"连不上 {base}：{type(exc).__name__}。"
                                       "NapCat 开了吗？端口对吗？"}
    ms = int((time.time() - start) * 1000)
    if info.status_code in (401, 403):
        return {"ok": False, "detail": "NapCat 说 token 不对，对一下面板里的 Token", "ms": ms}
    try:
        data = info.json()
    except Exception:  # noqa: BLE001
        return {"ok": False, "detail": f"返回的不是 OneBot 数据（HTTP {info.status_code}）", "ms": ms}

    login = data.get("data") or {}
    online = None
    try:
        st = requests.get(base + "/get_status", headers=headers, timeout=3).json()
        online = (st.get("data") or {}).get("online")
    except Exception:  # noqa: BLE001
        pass
    return {
        "ok": str(data.get("status", "ok")).lower() == "ok",
        "ms": ms,
        "uin": str(login.get("user_id") or ""),
        "nickname": str(login.get("nickname") or ""),
        "online": online,
        "detail": "连上了，但她没登录" if login.get("user_id") in (None, 0) else "",
    }


# ---------------------------------------------------------------- 人格文件
def _persona_path(name: str) -> Path:
    clean = (name or "persona.md").replace("\\", "/").strip().lstrip("/")
    path = (ROOT / clean).resolve()
    root = ROOT.resolve()
    if root != path and root not in path.parents:
        raise ValueError("这个路径不在项目里")
    if path.suffix.lower() != ".md":
        raise ValueError("只能编辑 .md 人格文件")
    return path


def _persona_rules() -> dict:
    path = ROOT / "personas.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def persona_files() -> list[dict]:
    rules = _persona_rules()
    files = [ROOT / "persona.md"]
    folder = ROOT / "personas"
    if folder.is_dir():
        files += sorted(folder.glob("*.md"))
    # 妹妹那份人格也让人在这儿改：她不在同一个数据目录里，但文件还在项目里
    sister_file = ROOT / "sister" / "persona.md"
    if sister_file.is_file():
        files.append(sister_file)
    default = str(rules.get("default") or "persona.md").replace("\\", "/")
    out = []
    for p in files:
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT).as_posix()
        sessions = [str(r.get("match")) for r in rules.get("rules", [])
                    if str(r.get("file", "")).replace("\\", "/") == rel]
        if rel == default:
            sessions.insert(0, "（没匹配到规则的会话）")
        if rel == "sister/persona.md":
            sessions = ["妹妹自己的那份（另一个进程）"]
        out.append({
            "file": rel,
            "label": "鱼丸（妹妹）" if rel == "sister/persona.md" else p.stem,
            "sessions": sessions,
            "size": p.stat().st_size,
            "mtime": datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M"),
            "is_default": rel == default,
        })
    return out


def read_persona(name: str) -> dict:
    path = _persona_path(name)
    if not path.is_file():
        raise ValueError("没有这份人格文件")
    return {"file": path.relative_to(ROOT).as_posix(),
            "text": path.read_text(encoding="utf-8")}


def write_persona(name: str, text: str) -> dict:
    path = _persona_path(name)
    if not path.is_file():
        raise ValueError("没有这份人格文件")
    backup_dir = DATA_DIR / "persona-backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        shutil.copyfile(path, backup_dir / f"{path.stem}-{stamp}.md")
        # 只留最近 30 份
        olds = sorted(backup_dir.glob("*.md"), key=lambda p: p.stat().st_mtime)
        for old in olds[:-30]:
            old.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
    path.write_text(text, encoding="utf-8")
    return read_persona(name)
