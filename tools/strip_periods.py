"""把历史回复里的句号清掉（只动 assistant 的消息，先备份）。"""
import re
import shutil
import sqlite3
import time
from pathlib import Path

DB = Path(r"D:\OneDrive\桌面\大肥鱼\data\fish.db")
BACKUP = DB.with_name(f"fish.db.bak-{time.strftime('%Y%m%d-%H%M%S')}")


def clean(text: str) -> str:
    out = []
    for line in (text or "").splitlines():
        line = re.sub(r"[。]+$", "", line.strip())
        line = line.replace("。", "，")
        line = re.sub(r"，{2,}", "，", line).strip("， ")
        if line:
            out.append(line)
    return "\n".join(out)


def main() -> None:
    shutil.copy2(DB, BACKUP)
    print(f"备份: {BACKUP.name}")

    conn = sqlite3.connect(DB)
    rows = conn.execute("SELECT id, content FROM messages WHERE role='assistant'").fetchall()

    changed = 0
    for row_id, content in rows:
        fixed = clean(content)
        if fixed != content:
            conn.execute("UPDATE messages SET content=? WHERE id=?", (fixed, row_id))
            changed += 1
    conn.commit()
    conn.close()
    print(f"检查 {len(rows)} 条，修改 {changed} 条")


if __name__ == "__main__":
    main()
