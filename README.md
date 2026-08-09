<div align="center">

# 🏛️ Hermes 微驿

### *微信多会话路由方案 —— Hermes Agent 专属*

![Hermes](https://img.shields.io/badge/Hermes-Agent-8B5CF6?style=flat-square&logo=data:image/svg%2bxml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCI+PHBhdGggZmlsbD0iIzhCNUNGNiIgZD0iTTEyIDJMMiA3djZsMTAgNSAxMC01VjdsLTEwLTV6TTQgOWwyIDF2NWwtMi0xVjl6bTE2IDBsLTIgMXY1bDItMVY5ek0xMiA0bDUgMi41LTUgMi41LTUtMi41IDUtMi41eiIvPjwvc3ZnPg==)
![Version](https://img.shields.io/badge/version-1.0.0-22c55e?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-f59e0b?style=flat-square)
![Platform](https://img.shields.io/badge/platform-iLink%20%E5%BE%AE%E4%BF%A1-3b82f6?style=flat-square)
![Made with](https://img.shields.io/badge/made%20with-%E2%9D%A4%EF%B8%8F%20%26%20AI-ef4444?style=flat-square)

> ### 🕊️ **神使掌驿，语有归途**
>
> 让微信成为你与所有 Hermes 会话之间的驿站——
> 一个窗口，路由万语；句句有归，聊无错乱。

---

</div>

## 📖 命名由来

**Hermes（赫耳墨斯）**——希腊神话中的**神使**：众神的信使、道路的守护者、跨越界域的引路人（*psychopomp*）。

这个方案让微信成为一座 **微驿**：Hermes 在此掌驿，把你的文字、图片、任务，像驿马传书一般，准确送到它该去的会话。对话永不失联，**每句话都有归途**——这正是 Hermes 的古老职能，在 AI 时代的回响。

## 📑 目录

- [背景与痛点](#-背景与痛点)
- [解决方案](#-解决方案)
- [核心特性](#-核心特性)
- [命令体系](#-命令体系)
- [快速开始](#-快速开始)
- [架构详解](#-架构详解)
- [配置项](#-配置项)
- [安全说明](#-安全说明)
- [已知限制](#-已知限制)
- [License](#-license)

---

## 🎯 背景与痛点

微信 iLink 机器人只有一个 DM 会话窗口（平台天花板），而你的 Hermes 可能有：

| 场景 | 问题 |
|:---|:---|
| 📅 多个 cron 定时任务推送 | 全挤在一个窗口，**分不清在回哪条** |
| 💬 桌面端多个进行中的会话 | 微信侧**看不到**、续接不上 |
| 🖼️ 图片/文件推送 | 媒体无法路由到指定会话 |
| 📶 iLink 出站限流 | 消息**直接失败**，回复石沉大海 |

> 💡 **本质矛盾**：微信是单窗口的，Hermes 是多会话的——需要一座"驿站"来转接。

---

## 🏗️ 解决方案

```
                 ┌──────────────────────────────────────┐
   微信 (iLink)  │        Hermes 微驿 (Gateway Hook)      │
                 │                                      │
  #0 目录         │  agent:start                         │
  #N 接力    ───► │   ├─ #0 目录    → 系统直发注册表       │
  #N 多媒体       │   ├─ #N 多媒体  → 锚定规则            │
  #删除 / #新建   │   └─ 图片检测   → 归属目标会话         │
                 │  agent:end                           │
                 │   └─ 对话回写   → user+assistant      │
                 │                                      │
                 │   📬 发送队列（FIFO + 重试，保证送达）  │
                 └──────────────────┬───────────────────┘
                                    │
                    ┌───────────────▼───────────────┐
                    │  📇 编号注册表 task_registry    │
                    │  #1 #2 #3 ... → 会话/任务映射   │
                    └───────────────┬───────────────┘
                                    │
                    ┌───────────────▼───────────────┐
                    │    目标会话（桌面 / cron）      │
                    └───────────────────────────────┘
```

---

## ✨ 核心特性

### 🎯 编号路由 —— 一眼识别，一号续接
- 每个任务/会话分配固定编号，投递消息首行显示 `#1 【完整名称】`
- 回复只需 `#1 继续`——**完整名称你记，编号我来分**
- 编号循环利用：删除即释放，新条目取最小可用

### 🔁 会话接力 —— 双向同步，无缝续接
- 桌面对话推送"接力卡"到微信，注册 `kind=session` 条目
- 微信侧续接后，hook 自动把对话**回写**到目标会话
- 回写为独立双消息（user 提问 + assistant 回复），与桌面轮次完全一致

### 🖼️ 多媒体锚定 —— 发图归位，不再迷路
- `#12 多媒体` 锚定 → 后续图片自动归属 #12
- 任意文字消息结束锚定
- 图片以路径引用回写，agent 可调用分析

### 📬 可靠投递 —— 限流不再丢消息
- 每会话 FIFO 队列 + 后台消费者，2.5s/条节奏发送
- 失败自动重试（30s × 3 次，覆盖 iLink cooldown）
- 全部机制化，**不依赖 LLM 自觉**

### 🛡️ 双保险 —— Hook 机制化 + Skill 协议
- Gateway Hook 在 `agent:start`/`agent:end` 强制执行
- 配套 Skill 为 agent 提供协议模板，LLM 自觉性不再是瓶颈

---

## 🕹️ 命令体系

| 命令 | 作用 | 示例 |
|:---|:---|:---|
| `#0` | 📋 列出全部会话/任务及编号（系统直发） | `#0` |
| `#N 内容` | 🔗 向 #N 会话接力/发消息 | `#3 现在进度如何` |
| `#N 多媒体` | 🖼️ 锚定：后续图片自动归属 #N | `#12 多媒体` |
| `#删除 N [-y]` | 🗑️ 删除条目（可批量，二次确认） | `#删除 5 8 -y` |
| `#新建 名称` | ➕ 新建会话（自动分配编号） | `#新建 服务器监控` |
| `#?` | ❓ 帮助 | `#?` |

---

## 🚀 快速开始

### 前置要求
- Hermes Agent（gateway 模式），已启用 weixin 平台适配器（iLink Bot）
- 微信侧完成 QR 登录（`hermes gateway setup`）

### 安装
```bash
# 1. 放置 Hook（用户目录，非镜像层，升级不丢）
mkdir -p $HERMES_HOME/hooks/weixin-handoff
cp hooks/weixin-handoff/* $HERMES_HOME/hooks/weixin-handoff/

# 2. 初始化编号注册表
cat > $HERMES_HOME/weixin/task_registry.json <<'EOF'
{
  "version": 2,
  "entries": [
    {"num": 1, "kind": "cron", "name": "示例监控任务", "job_id": "<YOUR_JOB_ID>"}
  ]
}
EOF

# 3. 重启 gateway 加载 Hook
hermes gateway restart

# 4. 验证：微信发 #0 应返回目录
```

### 使用示例
```
📱 你： #0
🤖 微驿： 📋 微信会话/任务目录
         #1 📅任务 监控 Hermes WebUI 解耦进展
         #2 📅任务 记忆容量看门狗
         #3 💬会话 微信多对话推送到PC解决方案

📱 你： #3 现在进度如何
🤖 微驿： （续接 #3 会话，回答后自动回写）

📱 你： #12 多媒体   → 📎 已锚定 #12
📱 你： [发送图片]   → 📎 已发送 1 张图片到 #12 会话
```

---

## 🧩 架构详解

```
$HERMES_HOME/
├── weixin/
│   ├── task_registry.json          # 📇 编号注册表（唯一事实来源）
│   └── accounts/                   # 🔑 iLink 凭证（适配器写入，勿动）
├── hooks/
│   └── weixin-handoff/
│       ├── HOOK.yaml               # ⚙️ 事件声明（agent:start + agent:end）
│       └── handler.py              # 🧠 目录/回写/锚定/发送队列
└── skills/
    └── weixin-task-routing/        # 📜 agent 协议 skill
```

### Hook 事件流

| 事件 | 入站消息 | Hook 动作 |
|:---|:---|:---|
| `agent:start` | `#0` | 系统直发目录，agent 静默 |
| `agent:start` | `#N 多媒体` | 写锚定状态 + 回执 |
| `agent:start` | 空消息 + 锚定存在 | 图片归属锚定会话并回写 |
| `agent:end` | `#N` | 对话（user+assistant）回写目标会话 |
| `agent:end` | 任意文字 | 清除锚定 |

---

## ⚙️ 配置项

| 参数 | 默认 | 说明 |
|:---|:---|:---|
| `SEND_INTERVAL_SECONDS` | `2.5` | 队列发送间隔（iLink 速率控制） |
| `SEND_RETRY_MAX` | `3` | 单条失败重试次数 |
| `SEND_RETRY_WAIT_SECONDS` | `30.0` | 重试间隔（应对 iLink cooldown） |
| `SEND_QUEUE_MAX` | `200` | 队列上限（防内存膨胀） |
| `MAX_REPLY_CHARS` | `8000` | 回写回复截断长度 |
| `ANCHOR_IMAGE_WINDOW` | `60` | 锚定图片检测时间窗（秒） |

---

## 🛡️ 安全说明

- ⚠️ **部署前必须脱敏**：注册表/凭证中的真实 `chat_id`、`job_id`、`account_id` 替换为占位符
- 🧿 iLink 机器人身份（`xxx@im.bot`）仅支持 DM，无法接收群消息（平台限制）
- 🛡️ 微信主会话条目禁止删除（Hook 内置保护）
- 📶 出站受 iLink 限流约束，由发送队列缓解

## ⚠️ 已知限制

- 微信侧仍是单窗口（平台天花板），编号是**逻辑隔离**而非物理多窗口
- 图片回写为"路径引用 + 描述文本"，桌面端是否渲染图片取决于客户端
- 发送队列为内存驻留，gateway 重启会清空待发队列
- 本方案运行于 `$HERMES_HOME/hooks/`（用户目录），不受 `/opt/hermes` 镜像层只读影响

---

## 📄 License

**MIT** —— 自由使用、修改、分发。

---

<div align="center">

**─── 🍲 ───**

*<sub>没有TG，不想多装客户端，只能出此下策，纯ai生成，没有一点点人工成分，原汤化原食，放心食用。</sub>*

**─── 🍲 ───**

</div>
