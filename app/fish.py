"""把人格、记忆、状态和模型缝成一条会说话的鱼。"""
from __future__ import annotations

import re
import threading
import time
from typing import Iterator

from . import emote, esports, peer, safety, self as selfedit, sky, websearch
from .brain import Brain
from .config import DATA_DIR, Settings
from .memories import MemoryStream
from .memory import FishMemory
from .persona import resolve as persona_path_for
from .persona import search_hint
from .persona import text_for as persona_text_for

# 命中这些词，就去网上看一眼再回答
LIVE_HINTS = (
    "今天", "昨天", "昨晚", "前天", "刚刚", "刚才", "最近", "现在", "最新", "今晚",
    "赛程", "比分", "谁赢", "赢了", "输了", "打谁", "几比几", "赛果", "赛况",
    "转会", "官宣", "签约", "离队", "加盟", "版本", "补丁", "夺冠", "晋级",
    "淘汰", "积分", "排名", "战绩", "出线", "谁强", "首发", "阵容",
    "八强", "四强", "决赛", "半决赛", "名单", "对阵", "比赛", "哪场", "那场", "几点",
)
# 这些词说明问的是赛程/比分，才值得去拉官方赛程页
SCHEDULE_HINTS = (
    "赛程", "比分", "谁打", "打谁", "几比几", "几点", "开赛", "对阵",
    "比赛", "赛果", "哪场", "那场", "谁赢", "赢了", "输了", "赛况", "名单",
)


def tidy_text(text: str) -> str:
    """聊天不打句号：把每行结尾的句号去掉。"""
    out: list[str] = []
    for line in (text or "").splitlines():
        line = re.sub(r"[。]+$", "", line.strip()).strip()
        if line:
            out.append(line)
    return "\n".join(out)


class Fish:
    def __init__(self, cfg: Settings, memory: FishMemory, brain: Brain,
                 memories: MemoryStream | None = None):
        self.cfg = cfg
        self.mem = memory
        self.brain = brain
        self.memories = memories or MemoryStream(DATA_DIR / "fish.db")
        self._sky_at = 0.0
        self._sky_text = ""

    # ---------- 她的窗 ----------
    def sky_line(self) -> str:
        """外面什么样。十分钟算一次就够，别每条消息都去查天气。"""
        now = time.time()
        if now - self._sky_at < 600:
            return self._sky_text
        try:
            self._sky_text = sky.sky_text() if (self.cfg.sky_city or self.cfg.sky_lat) else ""
        except Exception as exc:  # noqa: BLE001  算不出来就当她不知道
            print(f"[sky] 算不出来：{exc}")
            self._sky_text = ""
        self._sky_at = now
        return self._sky_text

    # ---------- 拼提示词 ----------
    def system_prompt(self, session: str, user_text: str = "",
                      self_change: str = "") -> str:
        persona = persona_text_for(session)
        parts = [persona, self.mem.state_prompt(session)]

        aware = selfedit.awareness(persona, self_change)
        if aware:
            parts.append(aware)

        outside = self.sky_line()
        if outside:
            parts.append(f"【她的窗】{outside}")

        # 她是谁的妹妹/姐姐这件事，不能靠「想起来」：记忆按会话分区，很容易答不知道
        rel = peer.relationship_hint()
        if rel:
            parts.append(rel)

        facts = self.mem.facts(session)
        if facts:
            parts.append("【你记得关于主人的事】\n" + "\n".join(f"- {f}" for f in facts))

        if self.cfg.memory_stream and user_text:
            recalled = self._recall(session, user_text)
            if recalled:
                parts.append("【你想起的事】\n" + recalled)

        summary = self.mem.summary(session)
        if summary:
            parts.append("【之前聊天的笔记】\n" + summary)

        parts.append(
            "【说话方式】这是微信式的闲聊，不是写作。"
            "一句为主，十来二十五个字；想说的多就分两三条，每条单独一行，别挤成一整段。"
            "可以反问、可以岔开、可以先应一声再补一句，也可以只回半个念头——别每条都老老实实回答。"
            "不总结、不分点、不用书面语，不要每句都拿问句收尾。"
            "不想多说就短一点，别硬凑，也别每轮都回一样长。"
        )

        if session.startswith("qq:"):
            extra = emote.prompt_block(session)
            if extra:
                parts.append(extra)

        if session.startswith("qq:group"):
            parts.append(safety.UNTRUSTED_NOTE)
        return "\n\n".join(parts)

    def _recall(self, session: str, user_text: str) -> str:
        """想起以前的事。跟主人聊天时，也想想跟妹妹（姐姐）聊过的。"""
        try:
            query = user_text
            # 她管对面叫「妹妹/姐姐」，主人说的是「鱼丸/鲸鲸」：补个别名，不然关键词对不上
            if "鱼丸" in query and "妹妹" not in query:
                query = query + " 妹妹"
            out: list[str] = []
            for i, sc in enumerate(peer.recall_scopes(session)):
                text = self.memories.recall_text(
                    query, scope=sc,
                    k=self.cfg.recall_k if i == 0 else max(1, self.cfg.recall_k // 3),
                )
                if text:
                    out.append(text)
            return "\n".join(out)
        except Exception as exc:  # noqa: BLE001  记忆坏了也不能让她不会说话
            print(f"[memory] 翻记忆失败：{exc}")
            return ""

    @staticmethod
    def _default_hint(user_text: str) -> str:
        """没配 search_hint 的会话：说到电竞就默认按 LPL 搜，说到别的球就让它自己搜。

        不加这一层，问「最近有什么转会」会被英超、NBA 的转会消息顶掉。
        注意别只凭「今天 / 昨晚」判断——问天气也带这词。
        """
        teams = ("LPL", "LOL", "BLG", "TES", "JDG", "WBG", "LNG", "RNG", "EDG", "FPX",
                 "IG", "OMG", "LGD", "NIP", "T1", "GEN", "DK", "HLE", "KC", "LYON", "G2")
        words = ("转会", "赛程", "比分", "战队", "选手", "版本", "补丁", "阵容", "首发",
                 "世界赛", "S赛", "八强", "四强", "那场", "昨晚")
        other = ("足球", "英超", "西甲", "意甲", "德甲", "中超", "欧冠", "NBA", "篮球", "世界杯")
        if any(w in user_text for w in other):
            return ""
        keys = teams + words
        return "英雄联盟 LPL" if any(w in user_text for w in keys) else ""

    def _search_query(self, user_text: str, extra: str) -> str:
        """把口语问题压成搜索词。

        以前只让它「不超过 18 个字」，模型常缩成一个「转会」——必应对这种单字词只会
        返「报价」「英雄」这类不相干页面；后来让它补日期，又补成「2026年11月 转会消息」，
        新闻搜索反而只返赛程。现在的规矩是：留实词、别自己加同义词、只有问「今天/现在」
        才加日期（新闻本身就按时间排，加月份只会变差），压得太短就退回原句。
        """
        core = websearch.clean_question(user_text)
        needs_today = any(w in user_text for w in ("今天", "今日", "今晚", "现在"))
        anchor = websearch.time_anchor() if needs_today else ""
        prompt = (
            "把下面的问题改写成一句必应搜索关键词，输出 2~6 个词、用空格分开。"
            "保留问题里的专有名词（队名、人名、游戏名、赛事名），用实词，别自己加同义词；"
            "不要加年份、月份、日期（问题里没说的就别加），不要标点，不要引号，不要解释。"
            + (f"\n今天是 {websearch.time_anchor()}。" if needs_today else "")
            + f"\n问题：{user_text}\n关键词："
        )
        query = ""
        try:
            query = self.brain.complete(
                [{"role": "user", "content": prompt}], temperature=0.1, max_tokens=40
            )
        except Exception:
            query = ""
        query = (query or "").strip().strip("。？！\"'“”").replace("\n", " ")
        query = re.sub(r"\s+", " ", query)
        # 「最近 / 今天」留在搜索词里，必应新闻常常一条都不返；时间靠排序和日期锚
        query = websearch.drop_time_words(query)
        # 压得太短、或者只是原句的复读，都说明没改好
        if len(query) < 4:
            query = core or user_text

        if extra and not any(tok in query for tok in extra.split() if tok):
            query = f"{query} {extra}"
        if anchor and anchor not in query:
            query = f"{query} {anchor}"
        return query

    def _live_block(self, session: str, user_text: str) -> str:
        if not self.cfg.websearch:
            return ""
        if not any(hint in user_text for hint in LIVE_HINTS):
            return ""

        extra = search_hint(session) or self._default_hint(user_text)
        blocks: list[str] = []

        # 只有问「赛程 / 比分 / 谁打」时才拿官方赛程，问转会、版本时它是纯噪音
        want_schedule = any(w in user_text for w in SCHEDULE_HINTS)
        if extra and want_schedule:
            try:
                data = esports.digest()
                if data:
                    blocks.append(data)
            except Exception as exc:  # noqa: BLE001
                print(f"[esports] {exc}")

        # 赛程之外的话题（转会、版本、阵容）得靠搜，赛程里没有的也补一条
        need_search = not blocks or any(
            w in user_text for w in ("转会", "版本", "补丁", "教练", "首发", "阵容", "签约", "官宣")
        )
        if need_search:
            try:
                query = self._search_query(user_text, extra)
                found = websearch.digest(query, hint=extra, question=user_text)
                if found:
                    blocks.append(found)
            except Exception as exc:  # noqa: BLE001
                print(f"[search] {exc}")

        return "\n\n".join(blocks)

    def build_messages(self, session: str, user_text: str,
                       image: str | None = None, self_change: str = "") -> list[dict]:
        msgs = [{"role": "system",
                 "content": self.system_prompt(session, user_text, self_change)}]
        msgs.extend(self.mem.recent(session, self.cfg.memory_window))
        content = user_text
        # 群里是陌生人写的字，框起来只当内容看
        if session.startswith("qq:group"):
            content = safety.fence(content, limit=300)
        block = self._live_block(session, user_text)
        if block:
            content = f"{content}\n\n{block}"
        if image:
            # 带图这一轮走多模态：文字 + 图一起交给模型
            parts = [{"type": "image_url", "image_url": {"url": image}}]
            if content:
                parts.insert(0, {"type": "text", "text": content})
            msgs.append({"role": "user", "content": parts})
        else:
            msgs.append({"role": "user", "content": content})
        return msgs

    # ---------- 对话 ----------
    @staticmethod
    def _display_text(user_text: str, image: str | None) -> str:
        """入库的文字：带图时留个记号，不然历史里看不出那句是配图的。"""
        if not image:
            return user_text
        return f"[图] {user_text}".strip() if user_text else "[图]"

    def _maybe_self_change(self, session: str, user_text: str) -> str:
        """主人说「你可以改改你自己」：当场想一次，这一句就按新的她来。

        群里不认——那是别人写的字，不能一句话让她改掉自己。
        """
        if session.startswith("qq:group") or not self.cfg.self_edit:
            return ""
        if not selfedit.wants_change(user_text):
            return ""
        try:
            got = selfedit.reflect(
                persona_path_for(session), self.brain, self.memories,
                cfg=self.cfg, session=session, source="chat", force=True,
            )
        except Exception as exc:  # noqa: BLE001  改不成也照常说话
            print(f"[self] 想自己没想成：{exc}")
            return ""
        if got.get("changed"):
            return str(got.get("text") or "")
        if got.get("why"):
            print(f"[self] {got['why']}")
        return ""

    def stream_reply(self, session: str, user_text: str,
                     image: str | None = None) -> Iterator[str]:
        user_text = (user_text or "").strip()
        if not user_text and not image:
            return
        # 先拼上下文（recent 里还没有这一句），再把这句话存进库，避免重复喂给模型。
        # 让她改自己要在拼上下文之前：这一句的回话就按新的她来。
        just = self._maybe_self_change(session, user_text)
        messages = self.build_messages(session, user_text, image, self_change=just)
        self.mem.add(session, "user", self._display_text(user_text, image))

        buf: list[str] = []
        try:
            for piece in self.brain.stream(messages, temperature=self.cfg.temperature):
                buf.append(piece)
                yield piece
        finally:
            text = tidy_text("".join(buf))
            # 入库的是「人看到的话」：把 [语音] / [表情:...] 这类发送标记去掉
            stored = emote.strip_tags(text) if text else ""
            if stored:
                self.mem.add(session, "assistant", stored)
                self.mem.bump(session, mood=0.8, affection=0.4)
                self._note(session, user_text or "[图]", stored)
                threading.Thread(
                    target=self._maybe_summarize, args=(session,), daemon=True
                ).start()

    def reply(self, session: str, user_text: str, image: str | None = None) -> str:
        return "".join(self.stream_reply(session, user_text, image)).strip()

    # ---------- 长期记忆压缩 ----------
    def _speaker(self, session: str) -> str:
        """记记忆时管对面叫什么。

        跟姐姐/妹妹（另一个 bot）聊，跟跟主人聊，是两回事：都记成「主人说」的话，
        两个人的话会混成一个人说的。
        """
        try:
            from . import peer
            if peer.is_peer_session(session):
                return (self.cfg.peer_name or "对面那个 bot").strip()
        except Exception:  # noqa: BLE001
            pass
        return "主人"

    def _note(self, session: str, user_text: str, reply: str) -> None:
        """往记忆流里记一句。出什么问题都不该影响聊天。"""
        if not self.cfg.memory_stream:
            return
        try:
            self.memories.note_exchange(session, user_text, reply,
                                        speaker=self._speaker(session))
        except Exception as exc:  # noqa: BLE001
            print(f"[memory] 记一句失败：{exc}")

    def tidy_memories(self) -> dict:
        """记模糊 → 合并 → 遗忘。配上模型才合并、才用模型改写（一次调用改一批）。"""
        return self.memories.tidy(
            rewrite=getattr(self.brain, "rewrite_memories", None),
            merge=getattr(self.brain, "merge_memories", None),
        )

    def _maybe_summarize(self, session: str) -> None:
        try:
            rows = self.mem.unsummarized(session, limit=60)
            if len(rows) < self.cfg.summary_after:
                return
            transcript = "\n".join(f"{r['role']}: {r['content']}" for r in rows)
            note = self.brain.summarize(transcript, self.mem.summary(session))
            if note:
                self.mem.save_summary(session, note, rows[-1]["id"])
                # 摘要也进记忆流：它本来就是「这几天发生过什么」，该会忘
                if self.cfg.memory_stream:
                    self.memories.add("note", note, scope=session, imp=4)
                print(f"[memory] 已压缩 {len(rows)} 条对话进长期笔记")
        except Exception as exc:
            print(f"[memory] 压缩失败：{exc}")
