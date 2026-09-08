<div align="center">

# 🏛️ Hermes 微驿

### *微信多会话路由方案 —— Hermes Agent 专属*

![Hermes](https://img.shields.io/badge/Hermes-Agent-8B5CF6?style=flat-square&logo=data:image/svg%2bxml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCI+PHBhdGggZmlsbD0iIzhCNUNGNiIgZD0iTTEyIDJMMiA3djZsMTAgNSAxMC01VjdsLTEwLTV6TTQgOWwyIDF2NWwtMi0xVjl6bTE2IDBsLTIgMXY1bDItMVY5ek0xMiA0bDUgMi41LTUgMi41LTUtMi41IDUtMi41eiIvPjwvc3ZnPg==)
![Version](https://img.shields.io/badge/version-1.2.0-22c55e?style=flat-square)
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
- 每会话 FIFO 队列 + 后台消费者，2s/条节奏发送
- 失败自动重试（70s 冷却 × 3 次，覆盖 iLink 熔断窗口）
- **账本补发监控器**：agent 回复限流失败入投递账本后，
  hook 每 15s 扫描 `delivery_obligations`，冷却结束立即重发，
  成功才标记 delivered——**不等 gateway 重启**
- 全部机制化，**不依赖 LLM 自觉**

### 🔥 熔断等待补丁 —— 根治限流风暴（v1.1.0 新增）
- iLink 限流时 weixin.py 原逻辑"熔断期内立即失败"→ 消息入账本等重启
- 补丁将两处失败点改为：**等待冷却结束（+2s 余量）再重试**
- 配合全局发送门闩，积压消息冷却后逐个排空，不扎堆、不丢消息
- **部署方式免疫容器更新**：补丁在 hook 加载时自动应用
  （`/opt/data/weixin_patch/` + hook 末尾调用），不碰 s6 启动脚本——
  `container_boot` 每次重生成的 run 脚本无需修改，补丁随 hook 永远生效
- 等待上限可配：`WEIXIN_RATE_LIMIT_MAX_WAIT_SECONDS`（默认 120s）

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

### 方式一：一键安装（推荐）
```bash
git clone https://github.com/nxcjs/hermes-weixin-router.git
cd hermes-weixin-router
bash install.sh          # 默认安装到 ~/.hermes，可指定: bash install.sh /path/to/hermes_home
hermes gateway restart   # 加载 Hook
```
安装脚本幂等安全：复制 Hook/Skill/脚本到 `$HERMES_HOME`，初始化注册表（不覆盖已有）。

### 方式二：手动安装
```bash
# 1. 放置 Hook（用户目录，非镜像层，升级不丢）
mkdir -p $HERMES_HOME/hooks/weixin-handoff
cp hooks/weixin-handoff/* $HERMES_HOME/hooks/weixin-handoff/

# 2. 放置 Skill
mkdir -p $HERMES_HOME/skills/weixin-task-routing
cp skills/weixin-task-routing/SKILL.md $HERMES_HOME/skills/weixin-task-routing/

# 3. 初始化编号注册表
mkdir -p $HERMES_HOME/weixin
cat > $HERMES_HOME/weixin/task_registry.json <<'EOF'
{
  "version": 2,
  "entries": [
    {"num": 1, "kind": "cron", "name": "示例监控任务", "job_id": "<YOUR_JOB_ID>"}
  ]
}
EOF

# 4. 重启 gateway 加载 Hook
hermes gateway restart
```

### 验证
微信发送 `#0`，应返回目录（空表时显示"注册表为空"）。

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

## 📜 更新日志

### v1.2.0 (2026-09-08)

**🧹 智能目录 —— #0 自动编号 + 死链自清理**

- **自动编号**：#0 触发时扫描全部活跃会话
  （未归档/未隐藏/有消息/30 天内活跃），
  没编号的自动分配最小可用编号——
  **无需手动发消息注册**
- **死链自清理**：#0 构建目录时校验每条目——
  cron 对照 jobs.json、session 对照 state.db，
  已失效条目自动从注册表移除并释放编号
  （校验失败时保守放行，不误删）
- **分组排序**：目录按「── 会话 ──」在前、
  「── 任务 ──」在后分组显示
- **排版修正**：中文条目不截断不手动换行，
  交给微信自动折行（手动换行会与微信折行打架）；
  底部提示拆为 ≤13 字短行，防末尾折叠
- **修复**：注册表编号重复（#15/#16 撞号），
  #删除 释放编号与自动分配的竞态防护

### v1.1.0 (2026-09-08)

**🔥 熔断等待补丁 —— 根治 iLink 限流丢消息**

- **修复**：`weixin.py` 原逻辑在熔断期内立即失败（`raise rate_limit_error`），
  消息入投递账本后要等 gateway 重启才补发；服务器限流(-2)触发熔断后
  直接 break 放弃重试
- **修复后**：两处失败点均改为"等待冷却结束（+2s 余量）再重试"，
  配合 `_send_text_gate` 全局门闩，积压消息冷却后逐个排空
- **新增** `weixin_patch/` 目录：
  - `weixin_patch_apply.py` —— 补丁实现（monkeypatch
    `WeixinAdapter._send_text_chunk_locked`，幂等，可独立调用）
  - `gateway_launcher.py` —— 可选的启动器方式（兼容参考）
- **部署革新**：补丁在 hook（`handler.py` 末尾）加载时自动应用，
  不再依赖 s6 启动脚本注入——`container_boot` 每次重生成的
  run 脚本无需修改，**容器更新 / gateway 重启均免疫**
- **新增** 账本补发监控器（handler.py 内）：
  - 每 15s 扫描 `delivery_obligations` 中 `state='failed'` 的微信记录
  - 经 hook 发送队列（2s 节奏 + 70s 冷却重试）立即重发
  - 成功才 `mark_delivered`，避免 gateway 重启时重复补发
- **调优**：hook 发送间隔 15s→2s（iLink 实测阈值 >10条/8s），
  重试等待 130s→70s（配合 60s 熔断时长）

### v1.0.0 (2026-08-09)

- 🎯 编号路由：任务/会话统一编号注册表
- 🔁 会话接力：桌面 ↔ 微信双向同步回写
- 🖼️ 多媒体锚定：`#N 多媒体` 图片自动归属
- 📬 可靠投递：FIFO 发送队列 + 失败重试
- 🛡️ Hook 机制化 + Skill 协议双保险
- 📦 一键安装脚本 `install.sh`

## 📄 License

**MIT** —— 自由使用、修改、分发。

---

<div align="center">

**─── 🍲 ───**

*<sub>没有TG，不想多装客户端，只能出此下策，纯ai生成，没有一点点人工成分，原汤化原食，放心食用。</sub>*

**─── 🍲 ───**

</div>
