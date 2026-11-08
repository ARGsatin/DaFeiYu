import sqlite3
from pathlib import Path

DB = Path(r"D:\OneDrive\桌面\大肥鱼\data\fish.db")
conn = sqlite3.connect(DB)

rows = conn.execute(
    "SELECT session, COUNT(*) FROM messages GROUP BY session ORDER BY COUNT(*) DESC"
).fetchall()
print("各会话消息数:")
for s, n in rows:
    print(f"  {n:>3}  {s}")
