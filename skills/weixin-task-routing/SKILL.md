---
name: weixin-task-routing
description: "微信消息以 #编号 开头时使用：检索对应任务/会话上下文并续接。"
version: 2.3.0
---

# 微信任务路由协议（WeChat Task Routing）

微信 iLink 机器人只有单会话（所有 DM 进同一个 `weixin:dm:<user>` 会话），
多个 cron 投递、会话接力混在一起时靠 **编号 + 完整标题** 区分：

- 投递消息首行：`#1 【示例监控任务】...`（编号 + 完整名称）
- 用户回复只需：`#1 继续跟进`（编号 + 内容）
- 会话接力：桌面对话推送到微信后分配编号，微信回 `#N xxx` 续接该会话

**唯一事实来源：`/opt/data/weixin/task_registry.json`**（编号注册表）。
所有编号分配/查询/释放都基于此文件，脚本投递时动态读取自己的编号和完整名称。

## ⚠️ 硬性规范（任何推送到微信的消息都必须遵守）

**每一次推送到微信的对话/消息，首行必须是 `#编号 【完整名称】`**，适用全部场景：
- cron 任务投递（no_agent 脚本输出、agent 型任务输出）
- 会话接力卡
- agent 主动推送的任何内容（一次性 cron、send_message 等）
- 完整名称定义：cron 任务 = jobs.json 里的完整 `name`；会话 = 会话标题（接力卡用源会话标题）。

格式模板：
```
#N 【完整名称】
<消息正文>
（回复 #N + 内容即可继续跟进）
```

## 0. 编号注册表操作规范

注册表结构：
```json
{
  "version": 2.1,
  "updated_at": "ISO时间",
  "entries": [
    {"num": 1, "kind": "cron", "name": "示例监控任务", "job_id": "0000000000000000", "note": "..."},
    {"num": 3, "kind": "session", "name": "会话完整标题", "session_id": "20260805_xxxx", "note": "..."}
  ]
}
```

- **分配编号**：读注册表，从 1 开始找**最小可用编号**（跳过已占用的 num），追加条目后写回。
- **释放编号**：删除对应条目即释放，编号**循环利用**（下次分配给新任务/会话）。
- **name 必须是完整名称**：cron 条目与 jobs.json 的 `name` 一致；session 条目用会话完整标题。
- **修改方式**：用 read_file 读、write_file 整体写回（JSON 文件，保持格式有效）。
- **脚本读取**：cron 脚本用 python3 读注册表按 `job_id` 找自己的 `num|name`（脚本已内置此逻辑）。

## 1. 用户回复带 `#编号` 时的路由流程

用户消息形如 `#1 现在什么情况？` 或 `#3 继续`：

1. **解析编号**：消息开头 `#` 后跟纯数字（如 `#1`、`#12`）。
2. **查注册表**：read_file `/opt/data/weixin/task_registry.json`，按 `num` 找条目：
   - `kind=cron` → 记下 `job_id`，走第 3 步；
   - `kind=session` → 记下 `session_id`，走第 4 步。
   - 注册表没有 → 用 session_search(query=标题关键词) 兜底；无果则告诉用户该编号不存在。
3. **cron 任务续接**：search_files(target=files, pattern="*", path="/opt/data/cron/output/<job_id>/")
   列出运行记录，读最新 `.md`；再 session_search(query=任务名, sort="newest") 找对话历史。
4. **会话接力续接**：session_search(session_id="<session_id>") 读该会话
   （返回首 20 + 尾 10 条消息），把握会话目标和当前进度。
5. **综合回答**：以上下文回答用户；需要"继续推进"就直接调用工具执行
   （agent 有完整工具权限，微信会话不是只能聊天的）。
6. **回写（双向同步）**：回答完成后按第 2.5 节把微信侧对话追加到目标会话记录，
   让桌面端能看到微信上聊了什么、桌面 agent 下次回复自带这段上下文。

## 1.5 目录命令 `#0`（列出全部会话/任务及编号）

**`#0` 由系统 hook 直接响应**（`/opt/data/hooks/weixin-handoff/` 在 `agent:start`
事件检测 `#0` 消息，直接从注册表构建目录并调用 iLink API 发送到微信），
**不需要 agent 输出目录**。

微信 agent 收到 `#0` 时：**保持静默，不要回复任何内容**（系统已自动发送目录）。
若 agent 发现用户还在等目录，可提示"目录已由系统发送，请查收"。

目录格式（hook 输出，agent 无需构造）：
```
📋 微信会话/任务目录
（回复 #编号 + 内容即可续接）

#1 📅任务 示例监控任务
#2 📅任务 示例提醒任务
#3 💬会话 示例接力会话

💡 新任务/新会话会自动分配编号；删除后编号循环利用。
在对应对话里说“推到微信接力”即可注册新会话。
```

## 2. 会话接力协议（桌面对话 → 微信续接）

触发场景：用户说"把这个会话推到微信接力" / "我走了，微信上继续这个事"。

执行步骤（在**源会话**里完成）：
1. 获取当前会话 ID：`echo $HERMES_SESSION_ID`（环境变量）。
2. **取会话完整标题**：session_search(session_id=上一步 ID) 看会话标题；
   或按当前对话主题拟一个完整标题（如"微信协议改造"→ 用会话真实标题优先）。
3. **分配编号**：按第 0 节规则在注册表追加 `{"num": <最小可用>, "kind": "session",
   "name": "<完整会话标题>", "session_id": "<步骤 1 的 ID>", "note": "..."}`。
4. **推送接力卡到微信**（首行必须带编号+完整名称）：创建一次性 cron：
   `cronjob(action="create", schedule="1m", repeat=1, deliver="weixin:<用户ID>@im.wechat",
   prompt="推送以下内容：#N 【<完整会话标题>】\n<当前进度摘要>...（回复 #N + 内容即可续接）")`
   注意：prompt 必须自带完整摘要（cron 会话无源会话上下文）。
5. 回复用户：已推送到微信，编号 #N。

微信侧收到接力卡后，用户回 `#N xxx` 走第 1 节流程第 4 步续接。

## 2.5 输出回写（微信 → 桌面/目标会话 双向同步）

**背景**：微信消息不会自动出现在桌面会话窗口（两套独立 session）。为让桌面端
看到微信上聊了什么，微信 agent 每次完成 `#N` 续接后，主动把对话追加到目标会话。

**回写工具**：`/opt/data/scripts/weixin_handoff_append.py`

```bash
python3 /opt/data/scripts/weixin_handoff_append.py <session_id> "<文本>"
# 或从 stdin 读文本；--preview 只预览不写入
```

- 用官方 `SessionDB.append_message(role="user")` 写入 state.db，安全。
- role 固定 `user`（mirror 语义）：桌面 agent 下次回复时能看到这段记录，
  且不破坏 assistant→assistant 交替。
- 文本格式建议：`[微信接力 <日期时间>] 用户问：... → 已回复：...`（带时间戳）。

**按条目类型回写**：
- `kind=session`：把文本追加到条目记录的 `session_id`（桌面会话）。桌面打开该会话
  即可看到；桌面 agent 后续回复自带微信进展。
- `kind=cron`：cron 会话每次运行是新 id（`cron_<jobid>_<ts>`），不适合追加。
  改为更新注册表条目的 `note` 字段（append 最新进展）+ 写 handoff 文件
  `/opt/data/weixin/handoff/<job_id>.md`（追加）。桌面 agent 查任务时读这些。

**回写时机**：每次 `#N` 续接对话有了实质结论/进展后执行；纯寒暄可不写。

## 3. 投递格式规范（所有发往微信的 cron 投递必须遵守）

- 首行格式：`#N 【完整名称】`（N 和名称都从注册表动态读取，禁止硬编码）。
- 末尾附一行：`（回复 #N + 内容即可继续跟进）`。
- no_agent 脚本任务：脚本 stdout 即消息，脚本内用 REGISTRY 动态取 `num|name`。
- 新建微信投递任务时：创建 cron 后**立即在注册表分配编号**并追加条目（name 用完整任务名）。

## 4. 在微信会话内创建任务（方案 B：attach_to_session）

当用户**在微信里**要求创建定时任务/提醒时：

- 用 `cronjob(action="create", ..., attach_to_session=true)`，**不要**显式传 deliver
  （默认 origin = 微信会话，投递才能 mirror 进会话）。
- 创建后**立即分配编号并追加注册表条目**（name 用完整任务名），然后告诉用户新编号。
- 原理：`attach_to_session=true` 且投递目标 == 任务创建会话时，cron 输出会作为
  user 角色消息写入该会话 transcript（gateway.mirror），用户下次回复时 agent
  自带 brief，不会"失忆"。
- 注意：显式 `deliver=weixin:<id>` 指向**其他**会话时不生效（fan-out 不 mirror）；
  桌面对话里创建、投递到微信的任务同样不 mirror。

## 5. 任务/会话删除时释放编号

- cron 任务被删除（cronjob action=remove）时：同步从注册表删除对应条目，编号释放。
- 会话接力到期/完成时：从注册表删除条目（可先保留 note 摘要到会话历史，编号释放）。
- 释放的编号下次分配给新条目（循环利用）。

## 6. 当前注册表内容（动态，以文件为准）

| #编号 | 类型 | 完整名称 | 定位键 |
|---|---|---|---|
| #1 | cron | 示例监控任务 | job_id=0000000000000000 |
| #2 | cron | 示例提醒任务 | job_id=1111111111111111 |

> 此表仅作速览；一切以 `/opt/data/weixin/task_registry.json` 为准。

## Pitfalls

- 微信单会话上下文会随投递增多而膨胀：回答完长任务后主动建议用户
  开新会话（微信里说"开新会话"让 agent 执行），避免上下文污染。
- iLink 限流：回复超长时拆多条发送，避免 30s cooldown。
- 用户不带 #编号 的普通消息按正常对话处理（当前会话上下文回答），
  不要强行套路由。
- 收到无法解析的 `#xx`（非数字）：按普通消息处理，并提示正确格式（`#编号 + 内容`）。
- 编辑注册表时保持 JSON 合法；写回后 read_file 复核一遍。
- 会话接力推送用一次性 cron 时，prompt 必须自带摘要，不能依赖"接力卡 agent 自己去读源会话"（cron 会话无源会话上下文；续接动作发生在微信侧 agent）。
- **任何推送微信的消息忘带编号+完整名称 = 违规**（用户明确要求），发现即补。
