"""大肥鱼的配置：路径、模型、语音。"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path


def _project_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _bundle_dir() -> Path:
    """只读资源在哪儿：打包后是 PyInstaller 的解包目录，源码运行就是项目目录。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return _project_dir()


def _user_dir() -> Path:
    """可写数据在哪儿：打包后放 %APPDATA%\\大肥鱼，源码运行还在项目目录（和以前一样）。"""
    override = (os.environ.get("FISH_HOME") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if not getattr(sys, "frozen", False):
        return _project_dir()
    base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
    return (Path(base) if base else Path.home()) / "大肥鱼"


BUNDLE_DIR = _bundle_dir()
ROOT = _user_dir()          # 可写：.env、data/、人格、表情包、模型
DATA_DIR = ROOT / "data"
WEB_DIR = BUNDLE_DIR / "web"  # 只读：网页
PERSONA_FILE = ROOT / "persona.md"


def _copy_file(src: Path, dst: Path) -> bool:
    try:
        if dst.exists() or not src.is_file():
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        return True
    except OSError:
        return False


def _copy_tree(src: Path, dst: Path) -> bool:
    try:
        if dst.exists() or not src.is_dir():
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst)
        return True
    except OSError:
        return False


def seed_user_dir() -> list[str]:
    """打包后的第一次运行：把旧数据搬到 %APPDATA%。

    先看 exe 旁边（很多人会把 exe 丢进原来的大肥鱼目录），再看向导里带的默认文件。
    只补缺的，不覆盖已有的。
    """
    if not getattr(sys, "frozen", False):
        return []
    # 明确指定了 FISH_HOME：这是「另一份存档」（妹妹那份），别往里塞主号的东西
    if (os.environ.get("FISH_HOME") or "").strip():
        return []
    ROOT.mkdir(parents=True, exist_ok=True)
    sources = [Path(sys.executable).resolve().parent, BUNDLE_DIR]
    moved: list[str] = []

    for name in (".env", "persona.md", "personas.json"):
        for src in sources:
            if _copy_file(src / name, ROOT / name):
                moved.append(name)
                break

    for name in ("personas", "stickers", "sister"):
        for src in sources:
            if _copy_tree(src / name, ROOT / name):
                moved.append(name + "/")
                break

    # 记忆库：没有才搬（带上 wal / shm，别把没落盘的对话丢了）
    if not (DATA_DIR / "fish.db").exists():
        for src in sources:
            if (src / "data" / "fish.db").is_file():
                for suffix in ("", "-wal", "-shm"):
                    _copy_file(src / "data" / ("fish.db" + suffix),
                               DATA_DIR / ("fish.db" + suffix))
                moved.append("data/fish.db")
                break
    return moved


SEEDED = seed_user_dir()


def load_env(path: Path | None = None) -> None:
    """极简 .env 加载，不引入额外依赖。已存在的环境变量优先。"""
    p = path or (ROOT / ".env")
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


load_env()

# 国内下载语音模型走镜像；自己有设置就不覆盖
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
# hf-mirror 不支持 Xet 存储后端，关掉才会走普通下载
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")


@dataclass
class Settings:
    host: str = os.getenv("FISH_HOST", "127.0.0.1")
    port: int = int(os.getenv("FISH_PORT", "8765"))

    # auto | ollama | api | offline
    provider: str = os.getenv("FISH_PROVIDER", "auto")

    ollama_base: str = os.getenv("OLLAMA_BASE", "http://127.0.0.1:11434/v1")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct")

    # 本地 transformers 模型目录；留空则自动扫描 models/ 下第一个可用的
    hf_model_dir: str = os.getenv("HF_MODEL_DIR", "")

    api_base: str = os.getenv("API_BASE", "https://api.deepseek.com/v1")
    api_key: str = os.getenv("API_KEY", "")
    api_model: str = os.getenv("API_MODEL", "deepseek-chat")
    # 越高越跳脱，越像人；太低会变成复读机
    temperature: float = float(os.getenv("FISH_TEMPERATURE", "0.95"))

    voice: str = os.getenv("FISH_VOICE", "zh-CN-XiaoyiNeural")
    # 音调越高越像小孩，语速稍快更活泼；edge-tts 的格式是 +25Hz / +8%
    voice_rate: str = os.getenv("FISH_VOICE_RATE", "+0%")
    voice_pitch: str = os.getenv("FISH_VOICE_PITCH", "+0Hz")
    stt_model: str = os.getenv("FISH_STT_MODEL", "small")

    # edge = 微软 edge-tts（免费）；breeze = BreezeBlue 云端音色（要 API Key，按字符计费）
    tts_provider: str = os.getenv("FISH_TTS", "edge").strip().lower()
    breeze_api_key: str = os.getenv("BREEZE_API_KEY", "").strip()
    breeze_api_base: str = os.getenv("BREEZE_API_BASE", "https://api.breeze.blue/v1").rstrip("/")
    # 博客里的「大肥鱼 · 迷糊版」
    breeze_voice_id: str = os.getenv("BREEZE_VOICE_ID", "voc_8yud7hz7c9q2").strip()
    breeze_model_id: str = os.getenv("BREEZE_MODEL_ID", "breeze-tts-2").strip()
    breeze_language: str = os.getenv("BREEZE_LANGUAGE", "zh").strip()
    breeze_instructions: str = os.getenv("BREEZE_INSTRUCTIONS", "").strip()
    # 0.5~2.0；小于 1 更慢。1.0 = 不额外处理
    breeze_speed: float = float(os.getenv("BREEZE_SPEED", "1.0") or 1.0)
    # 越高越听 instructions；默认 1.0
    breeze_guidance: float = float(os.getenv("BREEZE_GUIDANCE", "1.0") or 1.0)
    # 后期把音调压低一点（0.85~1.0），1.0 = 不处理；0.93 大约低一个半音
    breeze_pitch: float = float(os.getenv("BREEZE_PITCH", "1.0") or 1.0)
    # Breeze 每次生成的响度会飘：统一拉到目标 RMS，省得有的语音条小得听不见
    breeze_normalize: bool = os.getenv("BREEZE_NORMALIZE", "1") != "0"
    breeze_target_dbfs: float = float(os.getenv("BREEZE_TARGET_DBFS", "-18") or -18)
    breeze_peak_dbfs: float = float(os.getenv("BREEZE_PEAK_DBFS", "-1") or -1)

    # QQ 上除了打字，还能发语音条、发表情包（网页界面不受影响）
    voice_reply: bool = os.getenv("FISH_VOICE_REPLY", "1") != "0"
    sticker_reply: bool = os.getenv("FISH_STICKER_REPLY", "1") != "0"
    # 表情包目录；留空 = 项目下的 stickers/
    sticker_dir: str = os.getenv("FISH_STICKER_DIR", "")
    # 每天最多发几条语音 / 几个表情，两次之间至少隔几秒（你明确要的不受间隔限制）
    voice_max: int = int(os.getenv("FISH_VOICE_MAX", "6"))
    voice_gap: float = float(os.getenv("FISH_VOICE_GAP", "120"))
    sticker_max: int = int(os.getenv("FISH_STICKER_MAX", "12"))
    sticker_gap: float = float(os.getenv("FISH_STICKER_GAP", "90"))
    # 把 QQ 账号「收藏」里的表情同步到本地，让她也能发那些
    qq_faces: bool = os.getenv("FISH_QQ_FACES", "1") != "0"
    qq_faces_min: float = float(os.getenv("FISH_QQ_FACES_MIN", "10"))

    # NapCat / OneBot v11 的 HTTP API 地址，用于把消息发回 QQ
    qq_api: str = os.getenv("QQ_API", "http://127.0.0.1:3000")
    qq_token: str = os.getenv("QQ_TOKEN", "")
    # NapCat 装在哪：面板上的「启动 NapCat」用它；留空则自动找 D:\NapCat 等常见位置
    napcat_dir: str = os.getenv("NAPCAT_DIR", "")
    # 群里只在被 @ 时回复；设成 0 则每条消息都回（强烈不建议）
    group_at_only: bool = os.getenv("FISH_GROUP_AT_ONLY", "1") != "0"
    # 群聊自由发言：没被 @ 时她也判断要不要插一句（带严格限流）
    group_chat: bool = os.getenv("FISH_GROUP_CHAT", "1") != "0"
    # 只在这些群生效（逗号分隔群号）；留空或 * = 所有群
    group_allow: str = os.getenv("FISH_GROUP_ALLOW", "")
    # 同一个群每小时最多几句 / 两次之间至少隔几分钟 / 每天最多几句
    group_max_hour: int = int(os.getenv("FISH_GROUP_MAX_HOUR", "6"))
    group_gap: float = float(os.getenv("FISH_GROUP_GAP", "3"))
    group_max_day: int = int(os.getenv("FISH_GROUP_MAX_DAY", "15"))
    # 群里聊着的时候，她也可以自己插一句（不是等别人点名）
    group_chime: bool = os.getenv("FISH_GROUP_CHIME", "1") != "0"
    # 看了不说话的底噪：每秒最多看几条群消息（防止刷屏群把模型打爆）
    group_rate: float = float(os.getenv("FISH_GROUP_RATE", "6"))
    # 遇到「今天/赛程/比分」这类问题，先上网搜一眼再回答
    websearch: bool = os.getenv("FISH_WEBSEARCH", "1") != "0"

    # 任意微信桥接服务，把回复 POST 回去
    wechat_webhook: str = os.getenv("WECHAT_WEBHOOK", "")

    session: str = os.getenv("FISH_SESSION", "main")
    memory_window: int = int(os.getenv("FISH_MEMORY_WINDOW", "16"))
    summary_after: int = int(os.getenv("FISH_SUMMARY_AFTER", "30"))

    # 记忆流：按遗忘曲线变淡、想起来会变牢的那种（关掉就只剩摘要 + 事实）
    memory_stream: bool = os.getenv("FISH_MEMORY_STREAM", "1") != "0"
    recall_k: int = int(os.getenv("FISH_RECALL_K", "3"))
    # 多久整理一次记忆（记模糊 / 合并 / 遗忘），单位小时
    tidy_hours: float = float(os.getenv("FISH_TIDY_HOURS", "3"))

    # 她自己会变：人格文件末尾那一小段可以自己加 / 改 / 删，一次一条
    self_edit: bool = os.getenv("FISH_SELF_EDIT", "1") != "0"
    # 隔多久才想一次自己（小时）；到点也要攒够新事才真动
    self_edit_hours: float = float(os.getenv("FISH_SELF_EDIT_HOURS", "24") or 24)
    # 至少攒够几条新聊天才让她想，免得为一句气话就改自己
    self_edit_min: int = int(os.getenv("FISH_SELF_EDIT_MIN", "4") or 4)

    # 她的窗：填了城市（或坐标），她才知道外面天黑没黑、下没下雨
    sky_city: str = os.getenv("FISH_CITY", "")
    sky_lat: float = float(os.getenv("FISH_LAT", "0") or 0)
    sky_lon: float = float(os.getenv("FISH_LON", "0") or 0)

    # 主动发消息：她自己开口找你（QQ 私聊 + 网页界面，不碰群）
    proactive: bool = os.getenv("FISH_PROACTIVE", "1") != "0"
    # 每天最多几条（补话也算在里面）
    proactive_max: int = int(os.getenv("FISH_PROACTIVE_MAX", "3"))
    # 两次主动之间至少隔几小时
    proactive_gap: float = float(os.getenv("FISH_PROACTIVE_GAP", "3"))
    # 你多久没说话，她才会想起来找你（小时）
    proactive_idle: float = float(os.getenv("FISH_PROACTIVE_IDLE", "3"))
    # 聊到一半你没了下文：过多久她会自己补一句（分钟）
    proactive_follow: bool = os.getenv("FISH_FOLLOW", "1") != "0"
    proactive_follow_idle: float = float(os.getenv("FISH_FOLLOW_IDLE", "15"))
    # 在这个窗口里随机挑一个时刻补，免得每次都掐着表（分钟）
    proactive_follow_window: float = float(os.getenv("FISH_FOLLOW_WINDOW", "25"))
    # 一天最多补几句
    proactive_follow_max: int = int(os.getenv("FISH_FOLLOW_MAX", "2"))

    # ===== 妹妹（第二个 bot 账号）=====
    # 妹妹是另一份大肥鱼：数据在 sister/，自己一个端口、自己一套记忆。
    # 这里只管两件事：怎么把她拉起来、跟她聊天要不要踩刹车。
    sister: bool = os.getenv("FISH_SISTER", "0") != "0"
    sister_uin: str = os.getenv("FISH_SISTER_UIN", "").strip()
    sister_api: str = os.getenv("FISH_SISTER_API", "http://127.0.0.1:3001").strip()
    sister_token: str = os.getenv("FISH_SISTER_TOKEN", "").strip()
    sister_port: int = int(os.getenv("FISH_SISTER_PORT", "8766"))
    # 妹妹那份 NapCat 装在哪。留空就在鲸鲸那份旁边找 NapCat2。
    # 必须是「另一份」：面板上的「启动 NapCat」会跑那个目录里的 startNapCat.ps1，
    # 跑鲸鲸那份的脚本会先把本机所有 QQ 关掉，等于把鲸鲸踢下线。
    sister_napcat: str = os.getenv("FISH_SISTER_NAPCAT", "").strip()
    # 鲸鲸自己的 QQ 号：妹妹要知道「谁是姐姐」，留空就问 NapCat
    self_uin: str = os.getenv("FISH_SELF_UIN", "").strip()

    # 两个都接了模型的号聊起来会没完，这些是刹车（妹妹那份数据会被启动器自动写好）
    peer_uin: str = os.getenv("FISH_PEER_UIN", "").strip()
    # 主人自己的 QQ：妹妹那份数据只认「姐姐」和这个号。别人一律不理，免得小号被举报
    owner_uin: str = os.getenv("FISH_OWNER_UIN", "").strip()
    # 记记忆时怎么称呼对面那个 bot：妹妹那边写「姐姐」，鲸鲸这边写「妹妹」
    peer_name: str = os.getenv("FISH_PEER_NAME", "").strip()
    peer_open: bool = os.getenv("FISH_PEER_OPEN", "1") != "0"
    peer_max: int = int(os.getenv("FISH_PEER_MAX", "6"))
    peer_cooldown: float = float(os.getenv("FISH_PEER_COOLDOWN", "90") or 90)
    peer_day: int = int(os.getenv("FISH_PEER_DAY", "120"))
    peer_gap: float = float(os.getenv("FISH_PEER_GAP", "6") or 6)
    peer_quiet: str = os.getenv("FISH_PEER_QUIET", "23-9")
    peer_open_gap: float = float(os.getenv("FISH_PEER_OPEN_GAP", "3") or 3)
    peer_open_day: int = int(os.getenv("FISH_PEER_OPEN_DAY", "3"))
    # 只跟对面那个 bot 说话，别人一律不理（妹妹那份数据默认开，免得小号到处回人）
    peer_only: bool = os.getenv("FISH_PEER_ONLY", "0") != "0"
    # 免打扰时段，23-8 表示 23 点到早上 8 点
    proactive_quiet: str = os.getenv("FISH_QUIET", "23-8")
    # 早晚定时问候：早上 / 晚上各一条，在下面这个窗口里随机一个时刻
    proactive_slots: bool = os.getenv("FISH_SLOTS", "1") != "0"
    proactive_morning: str = os.getenv("FISH_MORNING", "08:00")
    proactive_night: str = os.getenv("FISH_NIGHT", "22:00")
    # 窗口多少分钟（8:00 + 60 = 8:00~9:00 之间随机发）
    proactive_slot_window: float = float(os.getenv("FISH_SLOT_WINDOW", "60"))

    extra: dict = field(default_factory=dict)

    def persona_text(self) -> str:
        if PERSONA_FILE.exists():
            return PERSONA_FILE.read_text(encoding="utf-8")
        return "你是「大肥鱼」，一条住在电脑里的大胖鱼，说话短、口语化。"


settings = Settings()
DATA_DIR.mkdir(parents=True, exist_ok=True)
