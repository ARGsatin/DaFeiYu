from app.brain import Brain
from app.config import settings
from app.fish import Fish
from app.memory import FishMemory
from app import websearch

mem = FishMemory(r"D:\OneDrive\桌面\大肥鱼\data\diag2.db")
fish = Fish(settings, mem, Brain(settings))

for q in ["今天有LPL比赛吗", "BLG最近赢了没"]:
    print("=" * 30)
    print("问:", q)
    print("改写后:", repr(fish._search_query(q, "英雄联盟 LPL")))
    block = fish._live_block("qq:group:235069995", q)
    print("喂给模型的资料:\n", block[:600])
