# 允许AI修改自身人格的实施方案

- 会话 ID：`session-e8bd2167-feb4-471d-8cd3-3cbc64534a10`
- 工作区：``
- 创建时间：
- 记录时间跨度：2026-11-05 13:58:58 ~ 2026-11-05 21:43:41
- 恢复说明：从被覆盖的 zstd 文件中逐帧抢救，部分消息可能缺失

---

## 👤 用户 · 2026-11-05 14:12:46

人是会变的，我希望她也是，可以给她权限对自己的人格做一些修改，让她做她自己。这个你看看具体怎么实施好？

**🤖 助手 · 2026-11-05 14:16:13**

I've got the picture. Let me set up a plan and start implementing.

**🤖 助手 · 2026-11-05 14:23:57**

我按「给她一小块真正能改的地方」来做，没有把整份人格交出去。整份人格里有你写的边界和说话规矩，如果允许模型每次重写，一次坏心情或一段群聊注入就可能把她改坏；只留一小段，她照样能慢慢长成她自己，你也能看见每一步、随时退回。

## 她怎么变

- 人格文件末尾会多一小段（`<!-- fish:self:begin -->` … `<!-- fish:self:end -->`），标题是「我自己改的（不是主人写的）」。**只有这一段会自己变**，`## 边界` 那些红线她碰不到；段里写着「跟前面不一样的地方以我这几条为准」，所以她能真的从「爱撒娇」长成别的样子。
- 攒够 `FISH_SELF_EDIT_MIN`（4）条新聊天、且隔够 `FISH_SELF_EDIT_HOURS`（24 小时）才回看一次；一次最多加 / 改 / 删一条，最多 12 条，满了得先删。
- 只从私聊和网页的记忆里长出来，`qq:group*` 的记忆直接排除。群友的话、搜索结果都进不来。
- 模型回 JSON（add / rewrite / drop / none），代码再检查一遍：超过 40 字、带第二人称命令、提「设定 / 边界」、像注入的，一律不落盘。
- 每笔改动记在 `data/self.json`（改前、改后、理由、是自动还是你让她想的）。面板「人格」页能看到时间线，一键「回到这之前」。
- 改完记一条 `kind=self` 的记忆，永不遗忘。过阵子她会自己想起来「我最近有点不一样」。
- 私聊 / 网页里说「你可以改改你自己」，她会当场回看一次，这一句回话就按新的她来；群里不认这句话。

## 用她现在的记忆试跑了一次（没落盘）

> 我好像真把每天问好晚安当成事了
> 理由：答应过为了小鱼干要记得，这两天一直在照做，慢慢就不是为了奖励了

不是换人设，是把经历长成自己。

## 怎么看 / 怎么用

重启一次大肥鱼（托盘 → 重启，或关掉 bat 再开），网页也刷新一下。重启约 90 秒后她会回看第一次（你库里现在有 11 件新事，够条件了）；如果她想出东西，会出现在 `persona.md` 末尾。不想让它一重启就动，先在面板「人格」页底部把「她自己会变」关掉。同一块地方也能点「让她现在想想」、看历史、退回。

## 改了哪些文件

- 新增 [self.py](app/self.py#L1)：那一小段、回看、校验、记录、退回
- [fish.py](app/fish.py#L240)：把「改改你自己」接到当场生效，并带进这一轮提示词
- [server.py](app/server.py#L236)：`/api/self` 系列 + 后台每半小时看一眼
- [prefs.py](app/prefs.py#L230)、[config.py](app/config.py#L221)：开关和频率
- [index.html](web/index.html#L1846)：人格页里的「🐟 她自己改的」
- [test_self.py](tests/test_self.py#L1)：22 个测试；全部 200 个测试通过
- [README.md](README.md#L346)、[.env.example](.env.example)

如果你平时开的是 `dist` 里的 exe，要重新打包 `tools/build_exe.ps1` 才会带上；源码启动不用。

另外，如果你想要的是「她可以整份重写、每次先给你看方案、你点头才生效」，那是另一种更放手的做法。要不要我再加一个「提方案」模式？

---

## 附录：工具调用时间线（共 74 次）

- `2026-11-05 14:12:48` **pwsh** "{\"description\": \"List workspace contents\", \"command\": \"Get-ChildItem -Force | Select-Object Mode,LastWriteTime,Length,Name | Format-Table -AutoSize\"}"
- `2026-11-05 14:12:50` **working_directory** "{}"
- `2026-11-05 14:12:54` **read** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\app\\\\persona.py\"}"
- `2026-11-05 14:12:54` **read** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\app\\\\prefs.py\"}"
- `2026-11-05 14:12:55` **read** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\app\\\\brain.py\"}"
- `2026-11-05 14:12:55` **read** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\app\\\\memories.py\"}"
- `2026-11-05 14:12:57` **read** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\app\\\\fish.py\"}"
- `2026-11-05 14:12:57` **grep** "{\"pattern\": \"persona|self|人格\", \"path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\app\", \"include\": \"*.py\"}"
- `2026-11-05 14:13:07` **read** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\app\\\\emote.py\"}"
- `2026-11-05 14:13:07` **pwsh** "{\"command\": \"Get-ChildItem -Recurse -File web | Select-Object FullName,Length | Format-Table -AutoSize; Write-Host '--- docs ---'; Get-ChildItem -Recurse -File docs | Select-Ob…
- `2026-11-05 14:13:18` **grep** "{\"include\": \"README.md\", \"path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\", \"pattern\": \"^#{1,4} |人格|persona\"}"
- `2026-11-05 14:13:18` **grep** "{\"include\": \".env.example\", \"path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\", \"pattern\": \".\"}"
- `2026-11-05 14:13:20` **read** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\README.md\", \"offset\": 77, \"limit\": 30}"
- `2026-11-05 14:13:20` **read** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\README.md\", \"offset\": 325, \"limit\": 160}"
- `2026-11-05 14:13:21` **grep** "{\"include\": \"index.html\", \"path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\web\", \"pattern\": \"persona|人格|记忆|memories\"}"
- `2026-11-05 14:13:24` **read** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\web\\\\index.html\", \"offset\": 1740, \"limit\": 130}"
- `2026-11-05 14:13:24` **read** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\tests\\\\test_prefs.py\"}"
- `2026-11-05 14:13:29` **read** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\personas\\\\qq-group-235069995.md\"}"
- `2026-11-05 14:13:29` **read** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\personas.json\"}"
- `2026-11-05 14:14:04` **read** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\web\\\\index.html\", \"limit\": 100, \"offset\": 880}"
- `2026-11-05 14:14:07` **grep** "{\"include\": \"index.html\", \"path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\web\", \"pattern\": \"\\\\.pbar|\\\\.pfile|\\\\.editor|\\\\.pactions|\\\\.pmeta|function cardEl|\\\\.setfo…
- `2026-11-05 14:14:09` **read** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\web\\\\index.html\", \"limit\": 70, \"offset\": 360}"
- `2026-11-05 14:14:09` **read** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\web\\\\index.html\", \"limit\": 60, \"offset\": 1240}"
- `2026-11-05 14:14:40` **read** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\app\\\\safety.py\"}"
- `2026-11-05 14:14:40` **read** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\app\\\\proactive.py\", \"limit\": 80}"
- `2026-11-05 14:14:42` **pwsh** "{\"command\": \"python --version; python -c \\\"import fastapi, openai; print('deps ok')\\\"; git status --porcelain 2>$null | Select-Object -First 5; if ($LASTEXITCODE -ne 0) { W…
- `2026-11-05 14:14:47` **read** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\tests\\\\__init__.py\"}"
- `2026-11-05 14:15:31` **grep** "{\"include\": \"*.py\", \"path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\", \"pattern\": \"system_prompt|build_messages\"}"
- `2026-11-05 14:16:07` **grep** "{\"include\": \"index.html\", \"path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\web\", \"pattern\": \"\\\\.mini|\\\\.cards|\\\\.card \"}"
- `2026-11-05 14:16:13` **todo_write** "{\"todos\": [{\"content\": \"Design and write app/self.py (self-editable persona section, reflection, log, revert)\", \"status\": \"in_progress\"}, {\"content\": \"Wire config fie…
- `2026-11-05 14:16:33` **write** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\app\\\\self.py\", \"content\": \"\\\"\\\"\\\"她的自我：让大肥鱼偶尔改一点点自己。\\n\\n人是会变的。这里给她的不是「重写人格」的权限，是一小块**她自己写得动**的地方：\\n\\n- 人格文件末尾有一小段，夹…
- `2026-11-05 14:16:36` **edit** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\app\\\\config.py\", \"old_string\": \"    # 多久整理一次记忆（记模糊 / 合并 / 遗忘），单位小时\\n    tidy_hours: float = float(os.getenv(\\\"FISH_TIDY_H…
- `2026-11-05 14:16:37` **edit** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\app\\\\fish.py\", \"new_string\": \"from . import emote, esports, safety, self as selfedit, sky, websearch\\nfrom .brain import Br…
- `2026-11-05 14:16:45` **edit** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\app\\\\fish.py\", \"new_string\": \"    # ---------- 对话 ----------\\n    @staticmethod\\n    def _display_text(user_text: str, ima…
- `2026-11-05 14:16:47` **edit** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\app\\\\server.py\", \"new_string\": \"from .adapters import qq, wechat\\nfrom . import (delivery, groupchat, napcat, pause, person…
- `2026-11-05 14:16:52` **edit** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\app\\\\server.py\", \"new_string\": \"@app.get(\\\"/api/persona\\\")\\ndef persona_get(file: str = \\\"persona.md\\\") -> dict:\\n…
- `2026-11-05 14:16:55` **edit** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\app\\\\server.py\", \"new_string\": \"def _tidy_loop() -> None:\\n    \\\"\\\"\\\"开机等一分钟，之后按 FISH_TIDY_HOURS 整理记忆：记模糊 → 合并 → 遗忘。\\…
- `2026-11-05 14:16:58` **edit** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\app\\\\server.py\", \"new_string\": \"    print(f\\\"  主动：{'开' if settings.proactive else '关'}\\\"\\n          f\\\"（每天最多 {setting…
- `2026-11-05 14:17:00` **edit** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\app\\\\server.py\", \"new_string\": \"    threading.Thread(target=_tidy_loop, daemon=True).start()\\n    threading.Thread(target=_…
- `2026-11-05 14:17:03` **edit** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\app\\\\prefs.py\", \"new_string\": \"    Field(\\\"FISH_TIDY_HOURS\\\", \\\"tidy_hours\\\", \\\"多久整理一次\\\", \\\"reply\\\", \\\"num…
- `2026-11-05 14:17:06` **edit** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\web\\\\index.html\", \"new_string\": \"  .pactions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }\\n\\n  /* --…
- `2026-11-05 14:17:09` **edit** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\web\\\\index.html\", \"new_string\": \"    persona = { file: d.file, text: d.text, files: d.files || [],\\n                self: d…
- `2026-11-05 14:17:15` **edit** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\web\\\\index.html\", \"new_string\": \"  row.append(save, revert, note);\\n  box.appendChild(row);\\n  if (persona.self) box.appen…
- `2026-11-05 14:17:43` **edit** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\tests\\\\test_self.py\", \"new_string\": \"    def reflect(self, reply: str, now: float | None = None, **kw):\\n        self.brain…
- `2026-11-05 14:17:45` **edit** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\tests\\\\test_self.py\", \"new_string\": \"        got = self.reflect('{\\\"action\\\":\\\"rewrite\\\",\\\"index\\\":2,\\\"text\\\…
- `2026-11-05 14:17:47` **pwsh** "{\"command\": \"python -m unittest tests.test_self -v 2>&1 | Select-Object -Last 60\", \"description\": \"Run self-edit tests\", \"workdir\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\"}"
- `2026-11-05 14:17:54` **pwsh** "{\"command\": \"python -m unittest discover -s tests -v 2>&1 | Select-Object -Last 45\", \"description\": \"Run full test suite\", \"workdir\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\"}"
- `2026-11-05 14:18:18` **pwsh** "{\"command\": \"$tmp = Join-Path $env:TEMP (\\\"fish-smoke-\\\" + [guid]::NewGuid().ToString(\\\"N\\\")); New-Item -ItemType Directory $tmp | Out-Null; Copy-Item persona.md (Join-…
- `2026-11-05 14:18:22` **write** "{\"content\": \"import json\\nimport sys\\n\\nfrom app import server\\nfrom app import self as selfedit\\n\\np = server.persona_get()\\nprint(\\\"persona keys:\\\", sorted(p.keys(…
- `2026-11-05 14:18:24` **pwsh** "{\"command\": \"$tmp = Join-Path $env:TEMP (\\\"fish-smoke-\\\" + [guid]::NewGuid().ToString(\\\"N\\\")); New-Item -ItemType Directory $tmp | Out-Null; Copy-Item persona.md (Join-…
- `2026-11-05 14:19:38` **edit** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\README.md\", \"new_string\": \"├─ data/fish.db      所有记忆都在这，拷走就是搬家\\n├─ data/self.json    她自己改自己的记录（能退回）\", \"old_string\": \"├─ d…
- `2026-11-05 14:19:40` **edit** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\README.md\", \"new_string\": \"测的是不联网、不碰真数据的部分：遗忘曲线 / 间隔效应 / 召回 / 记模糊 / 合并、日出日落与月相、防注入与 CQ 码清理、她自己改自己（只动那一小段 / 不认群里的话 / 能退回）。\", \…
- `2026-11-05 14:19:42` **pwsh** "{\"command\": \"$files = @(\\\"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\data\\\\_smoke_self.py\\\",\\\"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\data\\\\_probe_self.py\\\",\\\"D:\\\\OneDrive\\\\桌面\\\\…
- `2026-11-05 14:19:52` **edit** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\web\\\\index.html\", \"new_string\": \"    persona.files = d.files || persona.files;\\n    persona.text = d.text;\\n    persona.di…
- `2026-11-05 14:19:54` **pwsh** "{\"command\": \"node -v; python -c @\\\"\\nimport re\\nfrom pathlib import Path\\nhtml = Path('web/index.html').read_text(encoding='utf-8')\\nscripts = re.findall(r'<script[^>]*>(…
- `2026-11-05 14:20:01` **pwsh** "{\"command\": \"python -m compileall -q app tests; if ($LASTEXITCODE -eq 0) { Write-Host \\\"compile ok\\\" }; python -m unittest discover -s tests -t . 2>&1 | Select-Object -Last…
- `2026-11-05 14:20:21` **pwsh** "{\"command\": \"python -m unittest discover -s tests -t . 2>&1 | Select-String -Pattern \\\"Ran |OK|FAILED\\\"\", \"description\": \"Show test summary\", \"workdir\": \"D:\\\\OneD…
- `2026-11-05 14:20:38` **read** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\app\\\\fish.py\", \"limit\": 50, \"offset\": 62}"
- `2026-11-05 14:20:38` **read** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\app\\\\fish.py\", \"limit\": 75, \"offset\": 225}"
- `2026-11-05 14:20:44` **read** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\app\\\\server.py\", \"limit\": 80, \"offset\": 200}"
- `2026-11-05 14:20:44` **read** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\app\\\\server.py\", \"limit\": 55, \"offset\": 655}"
- `2026-11-05 14:20:47` **pwsh** "{\"command\": \"Select-String -Path DaFeiYu.spec -Pattern \\\"hiddenimports|collect|app\\\\.\\\" -Context 1,2\", \"description\": \"Check PyInstaller spec for module collection\",…
- `2026-11-05 14:20:54` **edit** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\app\\\\self.py\", \"new_string\": \"        \\\"你是「大肥鱼」，一条住在电脑里的小姑娘鱼。你一个人待着，翻自己这段时间的事。\\\\n\\\\n\\\"\", \"old_string\": \"        …
- `2026-11-05 14:21:01` **edit** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\app\\\\self.py\", \"new_string\": \"INTRO = (\\\"这几条是我自己慢慢改出来的，不是谁规定的；以后觉得不对，还可以再改。\\\"\\n         \\\"前面「边界」那几条红线不动，别的地方要是跟我现在写的不…
- `2026-11-05 14:21:06` **edit** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\app\\\\self.py\", \"new_string\": \"    return (f\\\"【刚刚】你刚在心里认下一件事：{just}。\\\"\\n            \\\"想顺着说就说，不想说也不用特地说。\\\")\", \"old_…
- `2026-11-05 14:22:43` **edit** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\README.md\", \"new_string\": \"人是一直在变的，她也可以。`persona.md` 末尾会有很小的一段——用 `<!-- fish:self:begin -->` 和\\n`<!-- fish:self:end -->` 夹着，标…
- `2026-11-05 14:22:49` **pwsh** "{\"command\": \"Get-ChildItem data -Filter \\\"self*\\\" | Select-Object Name,Length; Select-String -Path .env -Pattern \\\"FISH_SELF\\\" -SimpleMatch; Write-Host \\\"--- persona …
- `2026-11-05 14:22:55` **edit** "{\"file_path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\app\\\\server.py\", \"new_string\": \"def _self_target():\\n    \\\"\\\"\\\"她现在用的那份人格：她自己那一段就长在那儿。\\\"\\\"\\\"\\n    return person…
- `2026-11-05 14:22:57` **pwsh** "{\"command\": \"python -m compileall -q app tests; python -m unittest discover -s tests -t . 2>&1 | Select-String -Pattern \\\"Ran |^OK|FAILED\\\"\", \"description\": \"Final comp…
- `2026-11-05 14:23:17` **pwsh** "{\"command\": \"$tmp = Join-Path $env:TEMP (\\\"fish-view-\\\" + [guid]::NewGuid().ToString(\\\"N\\\") + \\\".md\\\"); Copy-Item persona.md $tmp; $env:PYTHONIOENCODING=\\\"utf-8\\…
- `2026-11-05 14:23:25` **pwsh** "{\"command\": \"Get-Content 大肥鱼.bat; Write-Host \\\"--- desktop head ---\\\"; Get-Content desktop.py -TotalCount 40\", \"description\": \"Check how the app is launched\", \"workdi…
- `2026-11-05 14:23:43` **grep** "{\"pattern\": \"def _maybe_self_change|/api/self|def selfBox|FISH_SELF_EDIT|## 她自己会变\", \"path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\", \"include\": \"*.py\"}"
- `2026-11-05 14:23:45` **grep** "{\"include\": \"index.html\", \"path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\\\\web\", \"pattern\": \"function selfBox|async function reflectSelf\"}"
- `2026-11-05 14:23:46` **grep** "{\"include\": \"README.md\", \"path\": \"D:\\\\OneDrive\\\\桌面\\\\大肥鱼\", \"pattern\": \"^## 她自己会变\"}"
