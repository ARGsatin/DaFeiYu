import sqlite3

DB = r"D:\OneDrive\桌面\大肥鱼\data\fish.db"
conn = sqlite3.connect(DB)
rows = conn.execute(
    "SELECT session, content, datetime(created_at,'unixepoch','localtime') "
    "FROM messages WHERE role='assistant' ORDER BY id DESC LIMIT 15"
).fetchall()

for session, content, ts in rows:
    flag = "HAS。" if "。" in content else "clean "
    print(f"{ts} [{session}] {flag} {content[:60]}")
