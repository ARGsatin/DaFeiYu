"""只清某个会话的消息（测试用）。用法：python tools/clear_session.py qq:group:235069995"""
import shutil
import sqlite3
import sys
import time
from pathlib import Path

DB = Path(r"D:\OneDrive\桌面\大肥鱼\data\fish.db")
TABLES = ("messages", "facts", "summaries", "state")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("用法: python tools/clear_session.py <session>")
    session = sys.argv[1]

    backup = DB.with_name(f"fish.db.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(DB, backup)
    print(f"备份: {backup.name}")

    conn = sqlite3.connect(DB)
    for table in TABLES:
        cur = conn.execute(f"DELETE FROM {table} WHERE session=?", (session,))
        print(f"  {table}: 删除 {cur.rowcount} 行")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
