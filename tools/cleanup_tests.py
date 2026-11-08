"""清掉测试会话，只保留网页主会话和真实私聊。"""
import shutil
import sqlite3
import time
from pathlib import Path

DB = Path(r"D:\OneDrive\桌面\大肥鱼\data\fish.db")
KEEP = {"main", "qq:private:2953133421"}


def main() -> None:
    backup = DB.with_name(f"fish.db.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(DB, backup)
    print(f"备份: {backup.name}")

    conn = sqlite3.connect(DB)
    sessions = [r[0] for r in conn.execute("SELECT DISTINCT session FROM messages").fetchall()]
    doomed = [s for s in sessions if s not in KEEP]

    for table in ("messages", "facts", "summaries", "state"):
        for s in doomed:
            conn.execute(f"DELETE FROM {table} WHERE session=?", (s,))
    conn.commit()

    left = conn.execute("SELECT session, COUNT(*) FROM messages GROUP BY session").fetchall()
    conn.close()

    print(f"删除 {len(doomed)} 个测试会话")
    print("剩余:")
    for s, n in left:
        print(f"  {n:>3}  {s}")


if __name__ == "__main__":
    main()
