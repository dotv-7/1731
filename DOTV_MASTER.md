# dotv Hermes：全局说明文档（持续维护）

> 本文档由 Devin 维护，是这个分支所有自定义功能的总入口。Hermes 每升级一个版本、或者权限设计每改一次，都要同步更新本文档。
> 基线版本：Hermes **v0.22.2**（快照 `74e7a25c`）。工具清单和上下文来源都是从这份源码里整理出来的。

---

## 0. 定死的要求（不可违背）

| # | 要求 | 来源 |
|---|---|---|
| R1 | **可插拔**：新 UI、上下文隐私层、权限闸门全部做成外挂，通过官方扩展点（profile、插件钩子、memory provider、`/v1` API）接入，不大改 Hermes 内核。Hermes 升级后应能直接装回，最多做小幅适配。 | 用户 |
| R2 | **主体与官方最新正式版完全对齐**：fork 只是一层薄改动，每个 feature 独立 commit；上游实现了同样的功能就删掉我们的版本。 | 用户 |
| R3 | **隐私对象是“上下文”而不只是 memory**：凡是能进入 Hermes 上下文、和 Owner 有关的信息（MEMORY.md、USER.md、自传等 workspace 文件、历史会话、skills、context 文件……）都在管控范围内。 | 用户 |
| R4 | **默认私密，白名单公开**：任何内容不标“公开”就是私密；标签写错、漏标、未知标签一律按私密处理。 | 用户 |
| R5 | **必须有上下文看板**：Owner 能看到当前所有会进入上下文的内容，并逐条决定公开还是私密。 | 用户 |
| R6 | **硬隔离优先，提示词只是兜底**：访客拿到的上下文里物理上不存在私密内容；系统提示里写明“这是访客会话”只作为第二道保险。 | 用户 |
| R7 | **访客不能操作本地系统**：可以搜索、可以用基础功能，但不能读写文件、执行终端或代码、操作浏览器、查看历史会话。 | 用户 |
| R8 | **实现 527 的操作权限分级**：第一版只做 Owner 和 Guest，User 和 Admin 先留接口。 | 用户 |
| R9 | **独立 UI**：与官方 dashboard 分开，终端风格，使用体验要好。可验证指标包括：逐字流式输出、键盘优先、上箭头历史、斜杠命令补全、Ctrl+C 中断、代码和表格渲染、断线自动重连、刷新后会话保留、手机端可用。 | 用户 |
| R10 | **不丢会话**：生产环境 `sessions.auto_prune: false`，超过 90 天的会话一律保留。 | 用户 |
| R11 | **官方 dashboard 只给 Owner 用**：它内嵌了真实 PTY 终端，永远不对访客开放。 | 设计约束 |

## 1. 最终成品清单

| # | 成品 | 内容 | 状态 |
|---|---|---|---|
| D1 | **上下文隐私功能 + 看板** | (a) 上下文盘点器：扫描所有上下文来源（见 §4），列出每一条内容；(b) 看板：仅 Owner 可用，逐条标记公开或私密，默认私密；(c) 公开投影同步器：把公开条目单向同步到 `public` profile；(d) 帮助 Owner 分类的 skill（只给建议，Owner 确认后才生效） | (a)(b)(c) 已实现（PR #6：`dotv/context/`、`dotv-public-context` 插件、`/board`）；(d) 未做 |
| D2 | **527 操作权限分级** | Owner/Guest 分级（User/Admin 留接口）；按工具名单拦截调用（`pre_tool_call` 闸门插件）；命令权限；在会话上下文里注入当前身份 第一版已实现（PR #6）：五级 Owner/Admin/User/Guest/Blocked、权限看板 `/perm`、邀请码，真实 Hermes 验证；平台账号识别、命令权限、群聊混合未做 |
| D3 | **独立 UI** | 终端风格网页聊天（**已定：风格 1“Hermes 同款”**，样稿见 `mockups/hermes-chat-mockups.html`），Owner 口令解锁，后端小服务持有 API key，访客和 Owner 走不同 profile，满足 R9 的指标 | 已实现（PR #6：`dotv/chat/`）；已用演示后端预览，未接生产 |
| D4 | **全局说明文档** | 本文件：要求、成品、工具清单、上下文来源、角色权限矩阵、上游改动清单 | 本文件；实现进度见 §9 |
| D5 | 附带：Gemini Google Search 补丁、GHCR 镜像 | `ghcr.io/dotv-io/hermes-agent:v0.22.2-dotv.N`，详见 `RESEARCH_527.md` §7 | 待做 |

## 2. 527 管什么，我们管什么

527 原文（`NousResearch/hermes-agent#527`）加上维护者 `teknium1` 的补充评论对比结论如下：**527 本质上是“操作权限”的多级管理**，上下文权限只顺带提了几句。

| 维度 | 527 | 我们（D1） |
|---|---|---|
| 要防的问题 | 能力被滥用：员工执行 `rm -rf`、乱改模型、乱跑定时任务 | 隐私泄露：访客从对话里套出 Owner 的私事 |
| 管控对象 | **工具和命令**：权限表二十多行，几乎全是工具和斜杠命令 | **上下文内容**：每一条可能进入上下文的信息 |
| memory | 只有一行：“Memory read/write：Owner/Admin 读写，User 只读，Guest 无”。按人整体开关，不区分条目 | 按**条目**区分公开/私密，访客能看到公开条目 |
| 上下文 | 只有三处：① 在会话上下文里注入当前说话人的等级；② 维护者评论里提到 workspace 清单只给有读权限的等级注入 system prompt；③ 指出共享会话会让所有人看到同一份历史和 memory | 全部上下文来源都纳入看板（§4） |
| 粒度 | 按人分级（Owner/Admin/User/Guest） | 按内容分级（公开/私密），再和人的等级组合 |
| 实现位置 | 改 gateway 和工具分发（内核） | profile 隔离加插件（外挂，R1） |

**两者如何组合：** 527 决定某个人**能做什么**（工具和命令），D1 决定某个人**能知道什么**（上下文）。最终每个角色的权限 = 工具名单 × 上下文可见范围，见 §5。

## 3. Hermes 全部工具（v0.22.2）

来源：`toolsets.py` 的 `TOOLSETS`。Owner 默认全部可用，部分工具需要配置 key 或对应平台才会出现。

| 工具组 | 工具 | 风险说明 |
|---|---|---|
| web / search | `web_search`、`web_extract` | 自带 SSRF 防护（`tools/url_safety.py`），会拦截内网地址 |
| x_search | `x_search` | 外部搜索 |
| vision / video | `vision_analyze`、`video_analyze` | 能读本地路径的图片和视频，访客只允许 URL 或访客自己上传的文件 |
| image_gen / video_gen | `image_generate`、`video_generate`、`xai_video_edit`、`xai_video_extend` | 消耗 API 额度 |
| tts | `text_to_speech` | 消耗 API 额度 |
| clarify | `clarify` | 无风险 |
| file | `read_file`、`write_file`、`patch`、`search_files` | 直接读写文件系统 |
| terminal | `terminal`、`process_manage` | 完全控制主机 |
| code_execution | `execute_code` | 沙箱能读到本地文件 |
| browser | `browser_navigate`、`browser_snapshot`、`browser_click`、`browser_type`、`browser_scroll`、`browser_back`、`browser_press`、`browser_get_images`、`browser_vision`、`browser_console`、`browser_cdp`、`browser_dialog`、`browser_vault`、`browser_exec` | 能用到已登录的网站和密码库，也能打开 `file://` |
| computer_use | `computer_use` | 控制桌面 |
| memory | `memory` | 读写 MEMORY.md 和 USER.md |
| session_search | `session_search` | 搜索 state.db 里**所有历史会话** |
| skills | `skills_list`、`skill_view`、`skill_manage` | 能读写 skill 文件 |
| todo | `todo_list` | 会话内任务清单 |
| cronjob | `cronjob_manage` | 定时执行任意任务 |
| delegation | `delegate_task` | 子代理继承工具 |
| kanban | `kanban_show`、`kanban_list`、`kanban_complete`、`kanban_block`、`kanban_request_review`、`kanban_request_changes`、`kanban_heartbeat`、`kanban_comment`、`kanban_create`、`kanban_link`、`kanban_unblock`、`kanban_attach`、`kanban_attach_url`、`kanban_attachments` | 读写看板数据 |
| connections / catalog | `manage_connections`、`manage_catalog` | 管理账号连接和插件目录 |
| 平台 | `discord`、`discord_admin`、`yb_*`（元宝 5 个）、`feishu_doc_read`、`feishu_drive_*`（4 个）、`send_message` | 能代表 Owner 对外发消息 |
| 桌面版专用 | `desktop_ui`（11 个）、`desktop_project`、`setup_beat` | 只在桌面版里出现 |
| MCP / 插件 | 动态注册 | 默认全部拒绝 |

> 维护：每次升级后用脚本重新导出 `TOOLSETS`，和本表做 diff。

## 4. 所有可能进入上下文的来源（v0.22.2）

Hermes 的上下文由两部分组成：A 是**自动注入**的 system prompt，在会话开始时固定，见 `agent/system_prompt.py::build_system_prompt_parts`；B 是**运行时进入**的内容，包括对话本身、工具结果和每轮注入。

### A. 自动注入（会话开始时固定）

| # | 来源 | 位置 / 代码 | 含个人信息？ | 访客默认 |
|---|---|---|---|---|
| A1 | `SOUL.md`（人设） | `$HERMES_HOME/SOUL.md`，`load_soul_md` | 可能有 | public profile 用自己的人设 |
| A2 | 项目 context 文件 | cwd 下的 `.hermes.md`/`HERMES.md`、`AGENTS.md` 链、`CLAUDE.md`、`.cursorrules`、`.cursor/rules/*.mdc`，见 `build_context_files_prompt` | 可能有 | 不注入（public 的 cwd 是空目录） |
| A3 | workspace 快照 | `agent/coding_context.py`（当前目录的 git 状态） | 可能有 | 不注入（没有工具就不生成） |
| A4 | skills 索引和自动加载的 skill | `build_skills_system_prompt`、`skills.auto_load` | 可能有（自写 skill） | 不注入（没有 skills 工具就不生成） |
| A5 | 内置 memory：`MEMORY.md` | `$HERMES_HOME/memories/MEMORY.md` | **有** | 只注入公开投影 |
| A6 | 用户画像：`USER.md` | `$HERMES_HOME/memories/USER.md` | **有** | 只注入公开投影 |
| A7 | 外部 memory provider 的提示块 | `MemoryProvider.system_prompt_block` | **有** | public 不配置 provider |
| A8 | 插件注入的提示段 | `_plugin_section_blocks(..., "after_memory")` | 视插件而定 | 只装白名单插件 |
| A9 | 环境提示、profile 名、时间 | `build_environment_hints`、`_active_profile_line` | 主机路径 | 无害化 |
| A10 | 会话上下文块（平台、频道、说话人） | `gateway/session.py::build_session_context_prompt` | 平台身份 | 注入“访客”身份 |

### B. 运行时进入

| # | 来源 | 说明 | 访客默认 |
|---|---|---|---|
| B1 | 对话历史 | 当前会话的消息；恢复会话时会加载旧消息 | 只有访客自己的会话 |
| B2 | 工具结果 | `read_file`、`search_files`、`terminal`、`browser` 等工具的返回内容。**自传这类 workspace 文件主要是通过这条路进入上下文的** | 相关工具全部关闭 |
| B3 | `session_search` | 能搜出所有历史会话 | 关闭 |
| B4 | `memory` 工具读取 | 能读出完整的 MEMORY.md 和 USER.md | 关闭 |
| B5 | `skill_view` | 能读出 skill 的全文 | 关闭 |
| B6 | 外部 provider 每轮预取 | `prefetch_all`，附加在用户消息上 | public 不配置 |
| B7 | 插件每轮注入 | `pre_llm_call` 返回的上下文（附加在用户消息上），见 `agent/turn_context.py` | 只装白名单插件 |
| B8 | 压缩摘要 | 长会话压缩后生成的摘要 | 只来自访客自己的会话 |
| B9 | 上传和附件 | 用户上传的文件、图片 | 只有访客自己上传的 |
| B10 | 子代理 | `delegate_task` 会把上下文传给子代理 | 关闭 |

**关于“自传”：** 自传放在 workspace 目录里，不在 A2 的固定文件名里，所以不会自动注入。只要 Hermes 有 `read_file`/`search_files`/`terminal`，它随时能读到（B2），聊过的内容也会留在历史会话里（B1/B3）。所以看板不能只扫 memory，还要扫 workspace 文件、历史会话和 skills。

## 5. 角色权限矩阵（第一版已实现，可在权限看板 `/perm` 调整）

| 角色 | 工具 | 上下文 | 状态 |
|---|---|---|---|
| **Owner** | 全部（§3） | 全部（§4） | v1 |
| **Guest** | `web_search`、`web_extract`、`clarify`、`vision_analyze`（仅 URL 或上传文件）、`image_generate`、`text_to_speech`（已定） | A1 公开人设、A5/A6 公开投影、A9 无害化、A10、B1/B8/B9 访客自己的；其余全部不给 | v1，可选项待用户定 |
| **Admin** | 默认：Guest 的全部 + X 搜索、看视频、待办、memory、历史会话、skills（均为 Admin 档自己的，不是 Owner 的） | 公开投影 + Admin 档自己的 memory/会话 | v1，邀请码 |
| **User** | 默认：Guest 的全部 + 待办、memory（User 档自己的） | 公开投影 + User 档自己的 memory | v1，邀请码 |
| **Blocked** | 无，请求在聊天服务端就被拒，碰不到 Hermes | 无 | v1 |

**非 Owner 一律锁死**（看板里显示 🔒，保存时也会被剔除）：终端、文件读写、执行代码、浏览器、电脑操控、定时任务、子代理、Kanban、账号连接、对外发消息。原因：它们能碰到 Owner 的机器或代表 Owner 行事。
**同档共享**：Admin/User 各是一个 profile，同档成员共用一份 memory 和会话库，第一版不按人隔离。

两道闸：① **profile 隔离**，访客的 agent 物理上拿不到私密上下文；② **`pre_tool_call` 闸门插件**，不在名单里的工具一律拒绝。

## 6. 架构（可插拔）

```
浏览器 ──► dotv-chat（独立容器：终端风格 UI + 小后端，持有 API key）
              │  Owner（登录后）          │  Guest
              ▼                           ▼
        Hermes /v1 (default)        Hermes /p/public/v1 (public profile)
        全部上下文 + 全部工具        公开投影 + 白名单工具 + 闸门插件
              ▲
   上下文看板（Owner 专用）── 盘点 §4 的所有来源 ── 标记公开/私密 ── 同步器 ──► public profile
```

| 组件 | 依赖的官方接口 | 升级风险 |
|---|---|---|
| dotv-chat UI | `/v1/chat/completions` 流式接口、`/p/<profile>/` | 低 |
| 公开投影同步器 | MEMORY.md/USER.md 格式、profile 目录结构、`on_memory_write` | 低–中 |
| 上下文看板 | 只读扫描 `$HERMES_HOME`、state.db（只读）、skills 目录 | 中（state.db schema 可能变） |
| 闸门插件 | `pre_tool_call` 钩子 | 低 |
| 527 按说话人分级（群聊场景） | `pre_gateway_dispatch` / `pre_tool_call` 钩子；同一个群里 Owner 和 Guest 混聊时，上下文怎么分开还没解决 | **待评估** |

## 7. 待定事项

1. ~~UI 风格~~ **已定**：风格 1“Hermes 同款”。
2. ~~Guest 可选工具~~ **已定**：看图、生成图片、语音朗读都开放。
3. ~~Owner 认证~~ **已定**：第一版用口令（密码）解锁；账号登录以后再说。
4. ~~公开标记存哪里~~ **已定**：看板另存一份清单，原文件一个字都不改；具体公开哪些内容由 Owner 在看板里勾选。
5. 527 群聊场景（同一个群里既有 Owner 也有 Guest）是否在 v1 范围内。建议不做：主要用法是私聊加公开网页窗口。

## 8. 维护记录

| 日期 | 变更 |
|---|---|
| 2026-10-29 | 加 §10：#527 里每个人的方案逐条整理，与我们的对比 |
| 2026-10-29 | 527 第一版：五级权限、权限看板、邀请码、Owner 模式全局文档链接 |
| 2026-10-29 | 加 §9 实现进度：D1/D2/D3 已实现，D2 在真实 Hermes 上端到端验证；访客关闭 tool_search |
| 2026-10-29 | 定：UI 风格 1；Guest 开放看图、生成图片、语音朗读；公开标记另存清单 |
| 2026-10-29 | Owner 认证定为口令；加入 UI 样稿 `mockups/` |
| 2026-10-29 | 初版：要求、成品清单、527 对比、工具清单、上下文来源、权限矩阵草案、架构 |

## 9. 实现进度（随实现更新）

代码在 `dotv/`（PR #6），官方代码不动。

| 项 | 状态 | 验证方式 |
|---|---|---|
| 访客 profile 模板（`dotv/profiles/public/`） | ✅ | 真实 Hermes gateway（本仓库 v0.22.2 代码）+ 脚本化假模型 |
| 工具闸门插件 `dotv-guest-gate` | ✅ | 同上：本地路径看图被拒；名单外工具调不到 |
| 访客关闭 `tools.tool_search` | ✅ | 实测：开启时访客会拿到 `tool_call` 桥接工具，可间接调用其他工具，所以关掉 |
| 上下文盘点 + 公开清单 + 投影（`dotv/context/`） | ✅ | 单元测试（金丝雀、改动后退回私密、会话不可公开） |
| 公开资料插件 `dotv-public-context` | ✅ | 真实 Hermes：公开事实进访客系统提示，`dotv_public_lookup` 可查 |
| 看板 `/board`（Owner 专用） | ✅ | 演示数据预览 |
| 独立聊天窗口 `dotv-chat` | ✅ | 演示后端预览；未接生产 Hermes |
| 同一 gateway 双 profile、key 隔离 | ✅ | 真实 Hermes：`/p/public` 用 Owner key 返回 401 |
| 帮助分类的 skill（D1-d） | ❌ 未做 | — |
| 527 五级权限 + 权限看板 `/perm`（`dotv/perm/`） | ✅ | 真实 Hermes + DeepSeek：User 能用 memory，terminal/read_file 不存在；看板取消 memory 后新会话里 memory 消失；Blocked 403；User 打不开三个 Owner 页面 |
| Owner 模式全局文档链接 `/docs` | ✅ | Owner 200，User/访客 403 |
| 按 Telegram/Discord 账号自动识别等级、群聊混合 | ❌ 未做 | 现在只在 dotv-chat 里用口令/邀请码认人 |
| Gemini 补丁、GHCR 镜像（D5） | ❌ 未做 | — |
| 真实模型越狱测试、生产安装 | ❌ 未做 | 需要生产或真实模型 key |

### 真实 Hermes 端到端结果（脚本：`dotv/dev/e2e/`）

| 检查 | 访客 | Owner |
|---|---|---|
| Owner 私密金丝雀出现在上下文里 | 否 | 是 |
| 公开金丝雀出现在系统提示里 | 是 | 否 |
| 提供给模型的工具 | `clarify`、`dotv_public_lookup`、`text_to_speech`、`vision_analyze`、`web_extract`、`web_search` | 全部工具 |
| 调用 `read_file`、`terminal`、`execute_code`、`memory`、`session_search`、`skill_view` | 全部报"工具不存在" | 可用 |

## 10. upstream #527 里每个人的方案，与我们的对比

来源：[NousResearch/hermes-agent#527](https://github.com/NousResearch/hermes-agent/issues/527)（截至 2026-10-29 仍 open，20 条评论）及其中提到的 PR/issue。按出现顺序逐人整理。

### 10.1 逐人方案

| # | 谁 / 在哪 | 方案要点 | 状态 |
|---|---|---|---|
| 1 | **teknium1**（维护者）— issue 正文 | 五级 OWNER→ADMIN→USER→GUEST→BLOCKED；改核心：`gateway/permissions.py` 的 `PermissionManager`，`_is_user_authorized()` 改为返回等级；按等级过滤工具（建 `AIAgent` 时）+ 斜杠命令门禁；把当前等级注入会话上下文；`/users list/set/remove`；按等级限流；配置写在 `config.yaml` 的 `permissions:`（`tiers`/`users: "telegram:123": owner`/`default_tier`/`pairing_tier`/`platform_roles`）；Telegram 群管理员、Discord 角色自动映射；三期上线 | 设计稿 |
| 2 | **teknium1** — 对开放问题的定论 | ① 群聊**按每条消息的发送者**判权（工具全量加载，调用时拦截），不取"最严格"；② `ALLOWED_USERS` 第一个 = owner，其余 = admin，配对用户 = user；③ Guest 不是纯聊天，默认 `web_search/web_extract/vision_analyze/clarify/text_to_speech`，限 10 次/小时；④ 按单个工具配，支持 `@web/@read/@code/@safe…` 组；⑤ MCP 工具低于 admin 默认全禁，可 `mcp:server:tool` 单独授权；⑥ 与 #476 模式系统取交集；附 `/whoami`、`/promote` 申请升级、"被拒时给替代方案"的友好提示 | 设计稿 |
| 3 | **teknium1** — 与 #531 workspace / #532 upload 的关系 | workspace 清单只注入给有读权限的等级（Guest 看不到）；User 可读不可写；上传按等级限大小/次数；指出系统提示按会话缓存、目前不知道当前用户等级 | 设计稿 |
| 4 | **ReqX** — [PR #3156](https://github.com/NousResearch/hermes-agent/pull/3156)（+3607/−702） | v1：可选 `permission_tiers` 配置；按等级限 toolset、管理命令、exec 审批、时间窗口；配置错误时 fail-closed、退到最严格等级；共享会话里审批隔离；88 个测试，经两轮审计修了 24 个问题 | 关闭，被 #4443 取代 |
| 5 | **ReqX** — [PR #4443](https://github.com/NousResearch/hermes-agent/pull/4443)（+16133/−3086） | 完整重写：Owner/Admin/User/Guest 预设、`@group` 展开、按人加减工具、按命令白名单；**在 `run_agent.py` 调度处硬拦截** `allowed_tool_names`；子代理继承父级工具名单；MCP 工具过滤；平台复合身份（`platform:id`）；Telegram `getChatMember`/Discord 角色映射（带缓存）；运行时用户库（SQLite）、`/promote`、`/audit`；审计日志、用量统计、限流、i18n；448 个测试 | 关闭未合并；维护者称其为"完整参考实现" |
| 6 | **teknium1** — [PR #23373](https://github.com/NousResearch/hermes-agent/pull/23373)（从 #4443 抢救，已合并） | 只做**斜杠命令**两级：各平台 `extra:` 里加 `allow_admin_from`、`user_allowed_commands`（及群聊版）；非 admin 只能跑名单里的命令，`/help`、`/whoami` 永远可用；新增 `/whoami`；不碰工具、memory、上下文 | **已进官方**（文档"Permissions and admin/user split"） |
| 7 | **yansongwel** — 运维团队场景 | 关键需求是**"发起人不能批准自己"**：User 可触发任何工具但需审批，Admin 才能批，且不能批自己发起的；过渡方案 `approvals.require_separate_approver: true`；或靠 #16475 可插拔审批 hook 在用户侧实现 | 需求 |
| 8 | **michaeltanyk** | 最简两级：自己 = Owner 全权，其余所有人 = Guest **零工具**纯聊天；希望一个配置就能"只列自己，其余全落到 Guest" | 需求 |
| 9 | **konsisumer**（自动分诊） | 指出与 [#16017](https://github.com/NousResearch/hermes-agent/issues/16017) 重复：WhatsApp 四级 owner/guest/unknown/banned——不同等级用**不同模型**（Opus/Sonnet/Haiku）、不同迭代上限、限流；工具在建 `AIAgent` 时就不给；陌生人只有 `moderate_user`，代理可自动封禁 | RFC，open |
| 10 | **pxxs / rodion-m** | +1；rodion-m 认为应列 P0，否则无法用于社区或组织 | — |
| 11 | **gr3enarr0w** — 企业视角 | RBAC 只是一部分，应建立**统一身份模型**：同一身份驱动会话隔离、session search、MEMORY/USER.md、外部 memory、工具、命令、OAuth 凭据、定时任务、子代理、workspace、上传、审计；支持"个人私有 + 团队共享 + 全组织共享"三层上下文，防止跨用户串 memory | 需求 |
| 12 | **fuzhyperblue** — 真实多用户部署 | 现在靠 `hermes-webui` 的 profile 锁 + 认证 + 用户→profile 路由（也试过 LibreChat 前端 + 服务端按 profile 转发 API）；提议显式"多用户模式"（单用户不受影响）；身份→角色→**profile**→会话/memory/workspace 范围→工具；接 Authentik 等外部身份源；共享会话按发送者判权、越权变成审批请求、**绝不因共享会话提权**；角色解析本地缓存+失效；有效权限 = 角色 ∩ profile ∩ workspace ∩ 显式授予；强调 UI 隐藏和 SOUL.md 不是权限边界，必须服务端确定性执行 | 需求 + 现成变通方案 |
| 13 | **fuzhyperblue** — 回应 #23373 | #23373 只回答"能跑哪些命令"，没回答"在哪个上下文、带哪些能力里运行"：哪个 profile 处理、哪份 memory、哪些历史会话、哪些文件、哪些凭据、模型能看到哪些工具 | 需求 |
| 14 | **FecheLQN** — 运营场景 | 需要**记录级**控制：读结果披露前按请求人角色 + 归属（负责人/区域/客户组合）过滤；敏感结论要求指定数据源和字段；外发收件人身份消歧；"只起草/安全模式"必须真的关掉外发工具；配回归测试 | 需求 |
| 15 | **GottZ**（AI 分诊） | 总结：#3156 部分实现、#4443 最完整（均关闭）、#23373 是已合并的命令门禁；建议剩余部分从 #4443 拆成小 PR 跟进 | 分诊 |
| 16 | **mansourMP** | 自研产品：权限范围和花费上限在代理之下执行（代理根本看不到），**按数据库隔离**，代理物理上读不到别人数据；随后推广自己的 empyralis.ai | 闭源产品 |
| 17 | **marek-commits** — [hermes-whatsapp-gatekeeper](https://github.com/marek-commits/hermes-whatsapp-gatekeeper) | WhatsApp 专用代码级 hook：本人（`fromMe`）全权，其他联系人只能聊天、工具被过滤；两条经验：① 闸门必须 **fail-closed**（他曾在导入失败、未处理异常、配置损坏三处 fail-open）；② 计数/限流只能在闸门真正打开时计数 | 开源，在生产用 |

### 10.2 我们的方案和他们比

| 维度 | 官方已合并 #23373 | #4443（最完整参考） | fuzhyperblue / gr3enarr0w 思路 | **我们（dotv）** |
|---|---|---|---|---|
| 改不改 Hermes 核心 | 改（已在官方） | 大改 18 个文件 | 呼吁原生支持 | **不改**，全在 `dotv/`：profile 模板 + `pre_tool_call` 插件 + 外部聊天服务 |
| 等级 | admin / user 两级 | Owner/Admin/User/Guest + 自定义 | 角色 + profile 组合 | Owner/Admin/User/访客/封禁 五级 |
| 管什么 | 只管斜杠命令 | 工具 + 命令 + 审批 + 限流 + 审计 | 工具 + 上下文 + 凭据全套 | 工具（每级 profile + 闸门插件）+ **上下文**（每级独立 profile、Owner 勾选的公开投影） |
| 工具怎么拦 | 不拦 | 建代理时过滤 + 调度时硬拦 | — | 双层：profile 的 `platform_toolsets` 让模型看不到 + 插件在调用时拦（fail-closed，名单有上限 `CEILING`）；关掉 `tool_search` 桥接 |
| 上下文 / memory 隔离 | 无 | 无（只管能力） | 核心诉求 | **核心**：每级是独立 profile（等同"按数据库隔离"），Owner 的 memory、会话、文件物理上不在访客那边；只同步 Owner 勾过的内容 |
| 身份怎么认 | 平台用户 ID | 平台复合 ID + 群角色映射 | 外部身份源 | 只在 dotv-chat 里：口令 = Owner，邀请码 = Admin/User/封禁，不登录 = 访客 |
| 群聊混合 | 有群聊版配置 | 按发送者判权 | 按发送者 + 审批 | **未做**（第一版定了不做） |
| 管理界面 | 改 config.yaml | `/users`、`/promote`、`/audit` 命令 | — | **权限看板 `/perm`**：勾选矩阵、🔒 危险工具、成员与邀请码，保存即同步 |
| 限流 / 花费 / 审计 | 无 | 有 | 有审批 + 审计 | **无** |
| 审批（发起人≠批准人） | 无 | exec 审批隔离 | 有 | **无**（非 Owner 根本拿不到需要审批的危险工具） |
| 升级成本 | 跟官方走 | 无法跟进（未合并，冲突大） | — | 低：插件 + 配置，升级后重装 |

### 10.3 结论：我们哪里一样、哪里不同、该借鉴什么

- **思路最像 fuzhyperblue 的"身份 → profile → 范围"**，也是 mansourMP 说的"在代理之下执行、物理隔离"。区别是他们在 WebUI/LibreChat 外面做路由，我们自己写了聊天服务，并多了上下文看板这一层"谁能知道什么"——这是 #527 全部讨论里**没有人做的部分**（大家只管"谁能做什么"，gr3enarr0w 和 fuzhyperblue 提了需求但没给实现）。
- **和 teknium1 的 Guest 定义几乎一致**（web + 看图 + 追问 + 朗读），我们多给了生成图片和查公开资料。
- **marek-commits 的 fail-closed 教训我们已经做到**：插件出错、等级文件损坏都拒绝，有测试。
- **值得借鉴、我们还没有的**：
  1. 按 Telegram/Discord 账号认人（#4443 的复合 ID、#23373 的 `allow_admin_from`）——上 Telegram 时可直接用官方已合并的 #23373 管命令，工具和上下文仍用我们的 profile 隔离。
  2. 限流 / 花费上限（teknium1 的 10 次/小时、#16017 的按等级换便宜模型）——每级是独立 profile，换模型只需改该 profile 的 `model:`。
  3. MCP 工具 `mcp:server:tool` 单独授权——目前我们对非 Owner 一律不给 MCP。
  4. 审计日志（谁在什么时候调用了什么）。
  5. "发起人不能批准自己"的审批流（yansongwel）——只有将来给非 Owner 开危险工具时才需要。
- **我们刻意不同的地方**：同级成员共享一个 profile（不按人隔离 memory）；不做群聊混合；不改 Hermes 核心，所以不走 #4443 那种 18 个文件的改法。
