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
| 2026-10-29 | 加 §10：#527 全部评论、关联 PR/issue 原文逐人整理，按维度和我们对比 |
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

## 10. upstream #527 里所有人的方案，与我们的完整对比

### 10.0 范围：读了哪些原文

全部原文都逐条读完，没有只看摘要：

- [#527](https://github.com/NousResearch/hermes-agent/issues/527) 正文和全部 20 条评论。截至 2026-10-29 状态为 open。
- 评论里出现的实现：[PR #3156](https://github.com/NousResearch/hermes-agent/pull/3156)、[PR #4443](https://github.com/NousResearch/hermes-agent/pull/4443)（含它的 `docs/feature-user-permissions.md`）、[PR #23373](https://github.com/NousResearch/hermes-agent/pull/23373)，这三个 PR 的描述和评论都读了；另有 [hermes-whatsapp-gatekeeper](https://github.com/marek-commits/hermes-whatsapp-gatekeeper) 的 README。
- 评论里提到的相关 issue：[#16017](https://github.com/NousResearch/hermes-agent/issues/16017)、[#16475](https://github.com/NousResearch/hermes-agent/issues/16475)、[#476](https://github.com/NousResearch/hermes-agent/issues/476)、[#531](https://github.com/NousResearch/hermes-agent/issues/531)、[#532](https://github.com/NousResearch/hermes-agent/issues/532)、[#410](https://github.com/NousResearch/hermes-agent/issues/410)、[#129](https://github.com/NousResearch/hermes-agent/issues/129)，正文和评论都读了。
- 官方已合并代码直接对照我们的 v0.22.2 源码：`gateway/slash_access.py`，以及 `website/docs/user-guide/multi-profile-gateways.md`。

本节用标签区分来源：**【原文】** 指 issue/PR 里写的内容，**【dotv 已做】** 指我们代码里已有的功能，**【dotv 未做】** 指我们还没有的功能。

### 10.1 每个人的方案

#### ① teknium1（维护者）：issue 正文，2026-03 的原始设计
- **背景【原文】**：gateway 现在只有"放行/拒绝"两种状态（`gateway/run.py` 中的 `_is_user_authorized()`），一旦放行就拿到全部工具，包括 terminal。
- **等级**：OWNER → ADMIN → USER → GUEST → BLOCKED。能力矩阵见 §5。User 对 memory 只读，Guest 没有任何工具。
- **识别身份**：用平台账号 `"telegram:123": owner` 这样的写法；`default_tier` 管没列出的人，`pairing_tier` 管配对进来的人；`platform_roles` 把 Telegram 群管理员、Discord 角色映射到等级。
- **在哪里拦**：新增 `PermissionManager`，`_is_user_authorized()` 改为返回等级；在命令分发前检查等级；创建 agent 时过滤工具；把等级注入会话上下文。
- **限流**：按等级设置，User 每小时 60 次，Guest 每小时 10 次。
- **管理方式**：`config.yaml` 加上 `/users list/set/remove` 命令。
- **自己列出的风险**：漏改一处就会出现绕过；群里多人共用一个会话怎么办；查平台角色有延迟；MCP 工具；和 #476 的关系。
- **分三期**：①等级、工具和命令；②限流和 `/users`；③平台原生角色。

#### ② teknium1：评论 1、2，对开放问题给出的定论（同一内容发了两次）
1. **群聊**：按**每条消息的发送者**判断权限。agent 加载全部工具，调用时再拦截；不采用"取群里最低权限"。每条消息都注入发送者的等级，被拒时 agent 能主动给出替代方案。
2. **向后兼容**：`*_ALLOWED_USERS` 里第一个人是 owner，其余是 admin；配对进来的人是 user。
3. **Guest 要有用**：默认开 `web_search, web_extract, vision_analyze, clarify, text_to_speech`，每小时 10 次。
4. **工具配置**：按单个工具配，也支持 `@web/@read/@write/@media/@code/@system/@memory/@skills/@safe/@all` 这些工具组。
5. **MCP**：admin 以下默认全部禁用，可以用 `mcp:server:tool` 或 `mcp:server:*` 单独授权。
6. **和 #476 模式系统**：实际可用工具 = 等级允许的 ∩ 当前模式允许的。
- **体验细节**：`/whoami`、`/promote` 申请升级（通知 owner）、被拒时给出能做什么、教对方自己做、或请 admin 代办。

#### ③ teknium1：评论 3，和 #531 workspace、#532 upload 的关系
- **workspace**：User 可以读、不能写；Guest **连 workspace 清单都不注入系统提示**，以免跨等级泄露。
- **上传**：按等级限制大小、超时和每天次数，Guest 不能上传。
- **他自己指出的问题**：系统提示按会话缓存，而会话按聊天划分、不按人划分，所以 `_build_system_prompt()` 拿不到当前用户的等级。他给的折中是"群里只要有人有权限就注入"。这种做法在上下文隐私上是有漏洞的。

#### ④ ReqX：[PR #3156](https://github.com/NousResearch/hermes-agent/pull/3156)，v1，+3607/−702，已关闭
- **配置**：可选 `permission_tiers` 配置，没配就完全不变。
- **按等级控制**：允许的 toolset、管理命令（`CommandDef.admin_only`）、能否批准执行、可用时间窗口。
- **加固**：配置写错、拼错、时间非法时 fail-closed，退到**最严格**的等级，而不是退成 admin；处理 YAML 布尔值和字符串混淆；共享会话里审批隔离，别人不能批你的执行请求。
- **测试与审计**：88 个测试，经一轮人工审计和一轮 4 个 LLM 交叉审计，修了 24 个问题（2 个严重、6 个高危）。
- **已知限制**：Mattermost 用的是可修改的用户名，不是稳定 ID，改名后等级映射会失效。
- 被 #4443 取代。

#### ⑤ ReqX：[PR #4443](https://github.com/NousResearch/hermes-agent/pull/4443)，完整重写，+16133/−3086，关闭未合并
- **改动面**：新增 `gateway/permissions.py`、`audit.py`、`usage.py`，另改 13 个核心文件，包括 `run.py`、`run_agent.py`、`delegate_tool.py`、`model_tools.py`、各平台适配器。
- **工具**：`allowed_toolsets` / `allowed_tools`（支持 `@group`），可按人加减工具。**创建 agent 前就把工具从 schema 里删掉**，模型看不到；调度时再拦一次；子代理继承父级的名单；MCP 低于 admin 默认禁用，过滤失败时 fail-closed。
- **命令**：`allow_admin_commands` 是一个开关，没有按命令的白名单，理由是逐条列太容易漏掉 `/stop`；另有 owner 专用命令，只有 owner 能授予 owner 等级。
- **身份**：`platform:id` 复合键，同一个数字 ID 在不同平台不会串；能从 `*_ALLOWED_USERS` 和配对自动推等级；`"*"` 兜底；`/users set` 运行时改等级（存 SQLite）。
- **平台角色**：Telegram 用 `getChatMember` 加 TTL 缓存；Discord 读角色和权限位。
- **限流**：按等级每小时 N 次，按整点分桶，**只计会触发 agent 的消息**，斜杠命令不计；`0` 等于封禁，但仍能用 `/whoami`。
- **其他**：时间窗口；`/promote` 申请和审批；`/audit` 审计日志（只追加的 SQLite，会轮转）；按人统计用量；多语言提示。
- **提示注入**：在系统提示里告诉 agent 当前等级、可用工具数、限制和限流，这是软性的一层。
- **自述的偏差**：memory 只能整体给或不给，做不到只读；`/model` 只在 CLI 有，不需要拦。
- **测试与审计**：448 个测试，5 轮审计（含多 LLM），修掉的问题包括 `/promote` 能自己批自己、MCP 过滤 fail-open、admin 能授予 owner。
- **结局**：维护者认为 16k 行的子系统太大，只把斜杠命令部分抢救进了 #23373。其余部分（工具过滤、审计、用量、限流、`/promote`、MCP 默认禁用）说等"有具体需求时"再分开提 PR。

#### ⑥ teknium1：[PR #23373](https://github.com/NousResearch/hermes-agent/pull/23373)，**已合并，我们的 v0.22.2 里就有**（`gateway/slash_access.py`）
- **只管斜杠命令**：每个平台的 `extra:` 里配 `allow_admin_from` 和 `user_allowed_commands`；群聊另有 `group_allow_admin_from` 和 `group_user_allowed_commands`，私聊和群聊互相独立。
- **规则**：admin 能用全部命令；普通人只能用白名单里的命令，加上永远可用的 `/help`、`/whoami`。插件注册的命令也受管。没配 `allow_admin_from` 时不启用。
- **多 profile**：多 profile gateway 下，由**收到消息的那个 profile** 的配置生效，fail-closed。
- **不管的东西**：工具、memory、上下文、限流、审计都不管，普通人照样能用全部工具。

#### ⑦ yansongwel：运维团队用 Telegram
- **问题**：开发者进了 `allowed_chats` 之后，可以自己点按钮批准自己的危险操作，审批形同虚设。
- **最小需求**：User 能触发工具，但需要审批；Admin 才能批，而且**不能批自己发起的**；Owner 全权。
- **过渡方案**：`approvals.require_separate_approver: true`；或者用 #16475 的可插拔审批 hook，把发起人 ID 和批准人 ID 都传进去。
- **后续**：#16475 已被维护者以"审批边界保持固定、不做可配置"为由关闭，这条路走不通了。

#### ⑧ michaeltanyk
- **需求**：只把自己设为 Owner 全权，其余所有人都落到 Guest，零工具、纯聊天、不能碰文件和终端。
- **配置期望**：一个配置能显式列出 guest，再加一个兜底把剩下的人都归进去。

#### ⑨ konsisumer（自动分诊，发了 3 次）指向 [#16017](https://github.com/NousResearch/hermes-agent/issues/16017)（LukaLeskovsek 的 WhatsApp 四级 RFC，open）
- **四级**：Banned（在 bridge 层静默丢弃，不花 token）、Unknown（陌生人：Haiku，只有 `moderate_user`，最多 5 轮，每小时 3 条、每天 10 条）、Guest（白名单号码：Sonnet，工具可配置，最多 15 轮）、Owner（Opus，最多 90 轮）。
- **工具在创建 agent 时就不给**；**每级可以配不同的模型、提示、skills 和最大轮数**；guest/unknown 的会话加前缀，防止看到 owner 的历史。
- **自动封禁**：agent 自己可以警告或封禁（`moderate_user`），每次都通知 owner；也有 `hermes whatsapp ban/unban/trust` 命令。
- 维护者把它标为"#527 在 WhatsApp 上的细化"。mansourMP 在这里也推广了自己的产品。

#### ⑩ pxxs、rodion-m
- 都是 +1，没有方案。rodion-m 认为应列为 P0：没有这个功能，Hermes 无法用于社区或组织内部。

#### ⑪ gr3enarr0w：企业视角
- **观点**：RBAC 只是统一的**身份与租户模型**的一部分。同一个已认证身份应同时决定以下各项：会话隔离、session search、MEMORY/USER.md、外部 memory、工具、命令、OAuth 凭据、定时任务、子代理、workspace、上传文件、审计。
- **上下文要分三层**：个人私有、团队/项目共享、全组织共享，同时防止跨用户串 memory 或串会话。
- **问维护者**：RBAC 最终只限于工具和命令吗？维护者没有回答。

#### ⑫ fuzhyperblue：评论 12，真实的公司多用户部署
- **现状**：靠 `nesquena/hermes-webui` 的 profile 锁、认证和"用户 → profile"路由；也试过 LibreChat 认证加服务端按 profile 转发 API，每个 profile 有自己的 SOUL、工具、skills、workspace 和 memory。能用，但他说是"一堆绕路方案"。
- **角色**：CTO = owner；IT = 高级用户，运维权限广，但碰不到全部密钥和全局配置；员工 = 普通用户。
- **提议**：
  - 显式的"多用户模式"，单用户模式不变；
  - 身份来源支持内置用户/组，也支持 Authentik 这类外部身份源；
  - 群聊**按发送者判权**，越权请求变成显式审批，**共享会话绝不能成为提权途径**；
  - 平台角色本地缓存，配置变化时失效，验证不了就对敏感工具 fail-closed；
  - 身份 → 角色 → **profile** → 会话/memory/workspace 范围 → 工具/命令；
  - 有效权限 = 角色 ∩ profile ∩ workspace ∩ 显式授予；
  - 分五期：多用户模式 → 外部身份源 → 审批和审计 → API/WebUI 复用。
- **强调**：UI 隐藏和 SOUL.md 只是护栏，不是权限边界，必须在服务端确定性地执行。

#### ⑬ teknium1：评论 13
- 回了一句"实现了一个简单版本"，链接到 #23373 的文档。

#### ⑭ fuzhyperblue：评论 14，回应 ⑬
- #23373 只回答了"这个人能跑哪些斜杠命令"。
- 没回答"这个人在哪个上下文、带哪些能力里运行"：由哪个 profile 处理、用哪份 Honcho 或内置 memory、能看哪些历史、能碰哪些文件、凭据和集成、模型能看到哪些工具、哪些定时任务属于他。

#### ⑮ FecheLQN：运营场景
- **记录级策略**：读结果披露前，按请求人角色和数据归属（负责人、区域、客户组合）过滤。
- **结论溯源**：说"没有""为零""无关联"之前，必须查过指定的数据源和字段。
- **外发收件人消歧**：拒绝有歧义的人和号码映射；记录解析过程；区分"本地已发送"和"平台已送达"。
- **"只起草/安全模式"要真生效**：必须真的收回外发工具，不能只是模型嘴上说。
- **配回归测试**：覆盖越权、溯源、收件人、安全模式这些场景。

#### ⑯ GottZ：AI 分诊
- #3156 是部分实现，#4443 是最完整的参考，两者都已关闭；#23373 是已合并的命令门禁。
- 建议把 #527 剩下的部分从 #4443 拆成小 PR 跟进。

#### ⑰ mansourMP：评论 17、18
- **自述**：自研产品，权限范围和**花费上限**在代理之下执行，代理"看不到所以绕不过"；**按数据库隔离**，一个代理物理上读不到另一个的数据。
- **其他**：之后推广自己的 empyralis.ai（基于 Claude SDK）。没有可复用的代码或细节。

#### ⑱ marek-commits：[hermes-whatsapp-gatekeeper](https://github.com/marek-commits/hermes-whatsapp-gatekeeper)
- **形态**：只针对 WhatsApp，是 `HERMES_HOME/plugins` 下的用户插件，挂在 `pre_gateway_dispatch` 钩子上，**不改核心，升级不受影响**。
- **身份**：本人（`fromMe`）全权，且本人一回复，AI 立即让出；其他人只能聊天，工具被过滤。
- **功能**：owner 超时未回复才由 AI 接手；按轮数封顶；拦截非 admin 的全部 `/` 和 `!` 命令。另有一个插件把官方默认放行的 `/help`、`/whoami` 也关掉，因为这两个命令会暴露命令列表。用正则拦截常见注入；拦截"我的资料、笔记是什么"这类打探；通过 Telegram 告警给 owner。
- **教训**：
  1. 必须 fail-closed，他曾在导入失败、未处理异常、配置损坏三处 fail-open；
  2. 计数器只能在闸门真正打开时计数。

### 10.2 横向对比（按维度）

| 维度 | ①② issue 设计 | ⑤ #4443 | ⑥ #23373（官方已合并） | ⑨ #16017 | ⑪⑫⑭ 企业诉求 | ⑱ gatekeeper | **dotv** |
|---|---|---|---|---|---|---|---|
| 改核心 | 是 | 是，改 16 个文件 | 是（已在官方） | 是，加 bridge | 要求原生 | **否，插件** | **否**：profile + 插件 + 外部聊天服务【dotv 已做】 |
| 等级 | 5 级 | 4 级 + 自定义 | admin/user | 4 级 | 角色 × profile | 2 级 | 5 级，后 3 级可配【dotv 已做】 |
| 身份来源 | 平台 ID | 平台复合 ID，可自动推 | 平台 ID | 手机号 | 内置或外部 IdP | `fromMe` | dotv-chat 口令（Owner）、邀请码（Admin/User/封禁）、不登录（访客）【dotv 已做】；平台账号【dotv 未做】 |
| 工具过滤位置 | 创建 agent 时 + 调度时 | 删 schema + 调度时 + 子代理继承 | 不过滤 | 创建 agent 时 | 服务端 | 按渠道 toolset | profile 的 `platform_toolsets` 删 schema，加 `pre_tool_call` 插件按 `tier.json` 拦截，名单有 `CEILING` 上限，关掉 `tool_search` 桥接【dotv 已做】 |
| 命令门禁 | 按等级白名单 | admin/owner 开关 | 按命令白名单 | CLI 命令 | 有 | 全拦 | 非 Owner 走 API 渠道，碰不到 gateway 斜杠命令；聊天窗口只有 `/unlock /lock /whoami` 等本地命令【dotv 已做】。上 Telegram 后可直接用 ⑥【dotv 未做】 |
| 会话/memory 隔离 | 不涉及（会话按聊天） | 不涉及 | 不涉及 | 会话加前缀 | **核心诉求** | 不涉及 | **每级一个独立 profile**：memory、会话、文件、`.env` 在磁盘上分开，API key 不同【dotv 已做】；同级不按人分【dotv 未做】 |
| "谁能知道什么" | ③ 只提了 workspace 清单不给 Guest | 无 | 无 | 无 | 提出需求 | 正则拦打探 | **上下文看板 + 公开清单 + 投影**：默认私密，原文变了自动退回私密，访客只能查到勾过的内容【dotv 已做，独有】 |
| 每级不同模型/人设 | 无 | 无 | 无 | **有** | profile 级 | 无 | 每级有独立 SOUL；可以配不同 `model:`，但看板里还没做【部分】 |
| 限流/花费 | 每小时 N 次 | 每小时 N 次，按整点分桶 | 无 | 每小时/每天 | 有 | 轮数封顶 | 无【dotv 未做】 |
| 平台角色 | 有 | Telegram/Discord，带缓存 | 无 | 无 | 缓存 + 失效 | 无 | 无【dotv 未做】 |
| MCP | 默认禁，可单独授权 | 默认禁，失败时关闭 | 无 | 无 | 有 | 无 | 非 Owner 不给 MCP（不在可配置清单里）【dotv 已做】；单独授权【dotv 未做】 |
| 审批 | 无 | 共享会话审批隔离 | 无 | 无 | 越权转审批 | 无 | 非 Owner 拿不到需要审批的危险工具，所以没有这个问题【dotv 已做】 |
| 审计 | 无 | `/audit` | 无 | 封禁通知 | 有 | Telegram 告警 | 无【dotv 未做】 |
| 管理界面 | `/users` | `/users`、`/promote` | `config.yaml` | CLI | IdP | JSON | **权限看板 `/perm`**：矩阵、🔒 标记、邀请码、封禁和删除即时生效【dotv 已做】 |
| 群聊混合 | 按发送者判权 | 按发送者判权 | 私聊/群聊分开 | 无 | 按发送者判权 + 审批 | 按发送者 | 无（第一版定了不做）【dotv 未做】 |
| fail-closed | — | 有 | 有 | — | 要求 | 有（踩过坑） | 插件出错、`tier.json` 损坏、`permissions.json` 损坏都拒绝，有测试【dotv 已做】 |
| 封禁 | BLOCKED | `requests_per_hour: 0` | 无 | bridge 层丢弃 | — | — | 在聊天服务层 403，请求到不了 Hermes【dotv 已做】 |
| 被拒时的提示 | 友好替代方案 | 系统提示注入等级 | — | — | — | 转移话题 | 每级 SOUL 写明身份和边界；没有工具时模型自己解释【dotv 已做（较简单）】 |
| 升级成本 | — | 无法跟进 | 跟官方走 | — | — | 低 | 低：插件 + 配置，升级后重装【dotv 已做】 |

### 10.3 结论

**我们和谁最像。** 最接近 fuzhyperblue 的"身份 → profile → 范围"和 mansourMP 的"在代理之下物理隔离"，形态上也像 marek-commits 的"插件、不改核心"。区别有两点：
- fuzhyperblue 是在 WebUI/LibreChat 外面做路由，我们是自己写的聊天服务；
- 我们多了"谁能知道什么"这一层（上下文看板和公开投影）。整个 #527 讨论里，只有 gr3enarr0w 和 fuzhyperblue 提了这个需求，**没有人给出实现**；官方合并的 #23373 也完全不涉及。

**我们比他们强的地方。**
1. **隐私边界是物理的**：#4443 和 issue 设计都是一个 profile 里删工具。只要 Guest 和 Owner 共用会话或 memory，就有 ③ 里 teknium1 自己承认的泄露风险（系统提示按聊天缓存）。我们是不同 profile、不同磁盘目录，Owner 的数据根本不在访客那边。
2. **访客不是零工具**：和 teknium1 "Guest 要有用"的思路一致，还多了查公开资料，能回答"他是谁"。
3. **fail-closed 和关桥接**：⑱ 踩过的坑我们都有测试覆盖；`tool_search` 桥接这个绕过点，#4443 的描述里没有提到。

**我们缺的，按优先级排**（全部是【dotv 未做】）：
1. **Telegram/Discord 账号识别**。可以写一个 `pre_gateway_dispatch` 插件（⑱ 证明这条路不改核心也行），把平台 ID 映射到等级再转给对应的 profile；命令部分直接用官方 ⑥ 的 `allow_admin_from`。注意：插件能不能把一条消息转到另一个 profile，还需要读代码验证。
2. **限流和花费上限**（①⑤⑨）：在聊天服务里按身份每小时计数，只计真正发给 Hermes 的请求（⑤⑱ 的教训）。另外在看板上给每级加一个"模型"选项（⑨）。
3. **审计日志**（⑤⑫）：记录谁、什么时候、哪一级、调用了哪些工具。
4. **MCP 单独授权**（②⑤）：`mcp:server:tool` 语法。
5. **同级按人隔离**（⑪⑫）：给每个成员单独建一个 profile。
6. **群聊混合**（①⑤⑫）：要按发送者判权，同时上下文不能串。这比只管工具难得多，因为同一个会话的上下文已经包含了 Owner 的内容。
7. **不打算做的**：发起人不能自批的审批流（⑦）。非 Owner 拿不到危险工具，暂时不需要；而且上游已经拒绝了可插拔审批（#16475）。记录级策略和结论溯源（⑮）属于业务数据层，不在我们的范围内。

### 10.4 和相关 issue 的关系

| Issue | 状态 | 和我们的关系 |
|---|---|---|
| [#476](https://github.com/NousResearch/hermes-agent/issues/476) 模式系统 | 已关闭 | 可用工具 = 等级 ∩ 模式。它的 allow/deny/ask 三种动作可以借鉴：以后给 Admin 开危险工具时，可以设为 ask |
| [#531](https://github.com/NousResearch/hermes-agent/issues/531) workspace | open | Guest 不注入 workspace 清单。我们的访客 profile 本来就没有 Owner 的 workspace |
| [#532](https://github.com/NousResearch/hermes-agent/issues/532) /upload | open | 按等级限上传。我们的聊天窗口第一版没有上传功能 |
| [#410](https://github.com/NousResearch/hermes-agent/issues/410) 密钥管理、[#129](https://github.com/NousResearch/hermes-agent/issues/129) 外泄威胁 | 已关闭 | 我们靠"每个 profile 有自己的 `.env`"加"非 Owner 没有 terminal/file"，让访客碰不到密钥 |
| [#16017](https://github.com/NousResearch/hermes-agent/issues/16017) WhatsApp 四级 | open | 见 ⑨；可借鉴"每级不同模型"和"bridge 层封禁" |
| [#16475](https://github.com/NousResearch/hermes-agent/issues/16475) 可插拔审批 | 已关闭（维护者拒绝） | ⑦ 的过渡方案走不通 |
