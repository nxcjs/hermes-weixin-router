"""微信 #编号 路由消息 hook（机制化，不依赖 agent 自觉）。

事件:
- agent:start + platform=weixin + 消息以 #0 开头
    → 直接从注册表构建目录，调用 iLink sendmessage 发送到微信
      （#0 是目录命令，系统直接响应，不让 agent 处理）
- agent:end + platform=weixin + 消息以 #数字 开头
    → 把该轮微信对话（用户问 + 微信侧回复）作为两条独立消息
      （role=user + role=assistant）追加到注册表指定目标会话，
      保持真正的对话样式，与桌面会话正常轮次一致。

- kind=session: append user 提问 + assistant 回复 到条目 session_id
- kind=cron:    更新注册表条目 note（cron 会话每次运行是新 id，不追加）

注册表: /opt/data/weixin/task_registry.json
"""

import asyncio
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "/opt/hermes")

REGISTRY = "/opt/data/weixin/task_registry.json"
MAX_REPLY_CHARS = 8000
# 存活标记文件：每次成功触发回写都更新，供看门狗验证 hook 是否活着
HEARTBEAT_FILE = "/opt/data/hooks/weixin-handoff/.heartbeat"
# 多媒体锚定状态文件：#N 多媒体 命令设置的锚定（图片自动归属目标会话）
ANCHOR_FILE = "/opt/data/weixin/handoff_anchor.json"
# 微信图片缓存目录（gateway 下载微信图片后存放）
IMAGE_CACHE_DIR = "/opt/data/cache/images"
# 锚定期间图片检测的时间窗（秒）
ANCHOR_IMAGE_WINDOW = 60
# iLink 账号凭证目录（Hermes 微信适配器写入）
ACCOUNT_DIR = Path("/opt/data/weixin/accounts")


def _touch_heartbeat(extra: str = ""):
    """记录 hook 触发痕迹，供外部看门狗巡检。"""
    try:
        with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()} {extra}\n")
    except Exception:
        pass


def _is_authorized_weixin(platform: str, chat_id: str) -> bool:
    """强校验：必须 platform=weixin 且 chat_id 在授权白名单。

    防止桌面端（platform=desktop/api_server）在微信会话里对话时
    误触发命令。仅真正来自微信平台的消息才返回 True。
    """
    if (platform or "") != "weixin":
        return False
    if not chat_id:
        return False
    # 读授权白名单（.env WEIXIN_ALLOWED_USERS 或 config platforms.weixin.extra.allow_from）
    import os

    allow_raw = os.getenv("WEIXIN_ALLOWED_USERS", "") or ""
    if not allow_raw:
        # 尝试从 config.yaml 读取
        try:
            import yaml

            cfg = yaml.safe_load(open("/opt/data/config.yaml", encoding="utf-8"))
            allow_raw = (
                (cfg.get("gateway", {})
                .get("platforms", {})
                .get("weixin", {})
                .get("extra", {})
                .get("allow_from", ""))
                or ""
            )
        except Exception:
            pass
    allowed = [x.strip() for x in str(allow_raw).split(",") if x.strip()]
    if not allowed:
        # 无白名单配置：回退到仅校验 platform=weixin（宽松模式）
        return True
    return chat_id in allowed


def _load_registry():
    try:
        with open(REGISTRY, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[weixin-handoff] 注册表读取失败: {e}", flush=True)
        _touch_heartbeat(f"ERROR registry:{e}")
        return None


def _find_entry(registry, num):
    for e in (registry or {}).get("entries", []):
        if e.get("num") == num:
            return e
    return None


def _build_catalog_text() -> str:
    """构建 #0 目录文本：注册表全部条目（编号 + 类型 + 完整名称）。"""
    data = _load_registry()
    entries = (data or {}).get("entries", []) or []

    lines = ["📋 微信会话/任务目录", "（回复 #编号 + 内容即可续接）", ""]
    if not entries:
        lines.append("（注册表为空，暂无编号条目）")
    for e in sorted(entries, key=lambda x: x.get("num", 0)):
        kind = "📅任务" if e.get("kind") == "cron" else "💬会话"
        name = e.get("name") or "?"
        lines.append(f"#{e.get('num')} {kind} {name}")
    lines += [
        "",
        "💡 新任务/新会话会自动分配编号；删除后编号循环利用。",
        "在对应对话里说“推到微信接力”即可注册新会话。",
    ]
    return "\n".join(lines)


def _account_credentials():
    """从微信适配器凭证目录读取 iLink 凭证（token/base_url/context_token）。"""
    if not ACCOUNT_DIR.exists():
        return None
    acct_file = None
    for f in sorted(ACCOUNT_DIR.glob("*.json")):
        if "context-tokens" in f.name or ".sync" in f.name:
            continue
        acct_file = f
        break
    if acct_file is None:
        return None
    try:
        acct = json.loads(acct_file.read_text(encoding="utf-8"))
        ct_path = ACCOUNT_DIR / (acct_file.stem + ".context-tokens.json")
        cts = (
            json.loads(ct_path.read_text(encoding="utf-8"))
            if ct_path.exists()
            else {}
        )
        return {
            "token": acct.get("token"),
            "base_url": acct.get("base_url")
            or "https://ilinkai.weixin.qq.com",
            "context_tokens": cts,
        }
    except Exception as e:
        print(f"[weixin-handoff] 读取微信凭证失败: {e}", flush=True)
        return None


# --- 发送队列（保证每条消息最终送达）：每 chat FIFO + 后台消费者 ---
SEND_INTERVAL_SECONDS = 2.5     # 消费者发送间隔（控制 iLink 速率）
SEND_RETRY_MAX = 3              # 单条失败重试次数（每次间隔 30s 等 cooldown）
SEND_RETRY_WAIT_SECONDS = 30.0  # 失败后等待（应对 iLink 30s cooldown）
SEND_QUEUE_MAX = 200            # 队列上限（防内存膨胀；正常不会触顶）


class _SendQueue:
    """每 chat 一个 FIFO 队列，后台消费者按节奏逐条发送，保证顺序与送达。"""

    def __init__(self):
        self._queues: dict = {}
        self._consumers: dict = {}
        self._history: dict = {}  # chat_id -> [monotonic, ...]（限流观察）

    def enqueue(self, chat_id: str, text: str) -> bool:
        if chat_id not in self._queues:
            self._queues[chat_id] = asyncio.Queue()
        q = self._queues[chat_id]
        if q.qsize() >= SEND_QUEUE_MAX:
            print(
                f"[weixin-handoff] 队列满({SEND_QUEUE_MAX})，丢弃: {chat_id[:12]}... {text[:30]}",
                flush=True,
            )
            return False
        q.put_nowait(text)
        self._ensure_consumer(chat_id)
        return True

    def _ensure_consumer(self, chat_id: str) -> None:
        task = self._consumers.get(chat_id)
        if task is None or task.done():
            self._consumers[chat_id] = asyncio.create_task(self._consumer(chat_id))

    async def _consumer(self, chat_id: str) -> None:
        q = self._queues.get(chat_id)
        if q is None:
            return
        while True:
            try:
                text = q.get_nowait()
            except asyncio.QueueEmpty:
                break  # 队列空，消费者退出；新消息入队时重新启动
            ok = await self._send_raw(chat_id, text)
            if not ok:
                for attempt in range(SEND_RETRY_MAX):
                    print(
                        f"[weixin-handoff] 发送失败，{SEND_RETRY_WAIT_SECONDS:.0f}s 后重试"
                        f"({attempt + 1}/{SEND_RETRY_MAX}): {text[:40]}",
                        flush=True,
                    )
                    await asyncio.sleep(SEND_RETRY_WAIT_SECONDS)
                    ok = await self._send_raw(chat_id, text)
                    if ok:
                        break
                if not ok:
                    print(
                        f"[weixin-handoff] 重试耗尽，丢弃: {chat_id[:12]}... {text[:40]}",
                        flush=True,
                    )
            await asyncio.sleep(SEND_INTERVAL_SECONDS)

    async def _send_raw(self, chat_id: str, text: str) -> bool:
        """实际调用 iLink sendmessage（凭证读取 + 发送）。"""
        creds = _account_credentials()
        if not creds or not creds["token"]:
            print("[weixin-handoff] 无微信凭证，发送失败", flush=True)
            return False
        try:
            from gateway.platforms.weixin import _send_message

            async with asyncio.timeout(30):
                async with _aiohttp_session() as session:
                    await _send_message(
                        session,
                        base_url=creds["base_url"],
                        token=creds["token"],
                        to=chat_id,
                        text=text,
                        context_token=creds["context_tokens"].get(chat_id),
                        client_id=f"hook-{uuid.uuid4().hex[:12]}",
                    )
            print(f"[weixin-handoff] 已发送到 {chat_id[:16]}...", flush=True)
            return True
        except Exception as e:
            print(f"[weixin-handoff] 发送失败: {e}", flush=True)
            return False


_send_queue = _SendQueue()


async def _send_weixin_text(chat_id: str, text: str) -> bool:
    """发送文本到微信（入队即返回，后台消费者保证送达）。

    - 同一 chat 消息 FIFO 顺序发送，间隔 SEND_INTERVAL_SECONDS 控制速率
    - 失败自动重试（30s 间隔 × SEND_RETRY_MAX），避免 iLink 限流丢消息
    - 不再阻塞 agent 流程（之前节流的 sleep 移除了）
    """
    return _send_queue.enqueue(chat_id, text)


def _aiohttp_session():
    import aiohttp

    return aiohttp.ClientSession()


# 命令注册表：单一事实来源，动态帮助从这里生成
# 每项: (触发正则前缀, 命令名, 说明)
COMMAND_DEFS = [
    (r"^#[?？]\s*$", "#?", "显示本帮助（动态生成，全角 #？ 也支持）"),
    (r"^#0\b", "#0", "查看会话/任务目录（全部编号）"),
    (
        r"^#(?:删除|删掉|移除|取消|去掉|清除|销毁)\b",
        "#删除 编号 [-y]",
        "删除会话（同义词：删掉/移除/取消/去掉/清除/销毁；-y 确认；可批量 #删除 5 8 13 -y）",
    ),
    (
        r"^#(?:新建|新增|创建|开|开启|建立)\b",
        "#新建 名称",
        "新建会话（同义词：新增/创建/开/开启/建立；不带名称自动命名）",
    ),
    (r"^#(\d+)", "#编号 内容", "接力会话：向 #编号 指向的会话发送消息"),
]


def _build_help_text() -> str:
    """动态生成帮助文本（基于 COMMAND_DEFS + 当前注册表状态）。"""
    data = _load_registry()
    entry_count = len((data or {}).get("entries", []) or [])
    lines = ["❓ 微信命令帮助", "（命令与参数之间必须有空格）", ""]
    lines.append("📌 可用命令：")
    for _, name, desc in COMMAND_DEFS:
        lines.append(f"  {name}")
        lines.append(f"      {desc}")
    lines += [
        "",
        f"📋 当前会话/任务：{entry_count} 条（发 #0 查看详情）",
        "",
        "💡 例：#5 你好 ｜ #删除 5 -y ｜ #新建 项目A",
    ]
    return "\n".join(lines)


async def _handle_catalog_command(context) -> None:
    """agent:start 时检测管理命令，直接响应。

    渠道分流：
    - 微信渠道（platform=weixin + 授权 chat）→ #0/#?/#删除/#新建 管理命令
    - 非微信渠道（desktop/api_server 等）→ #推到微信 接力命令（注册新会话）
    - 微信渠道收到 #推到微信 → 不触发（防与 #编号 混淆）
    """
    message = (context.get("message") or "").strip()
    chat_id = context.get("chat_id") or ""
    if not chat_id:
        return
    platform = context.get("platform") or ""
    is_weixin = _is_authorized_weixin(platform, chat_id)

    # --- "#推到微信"接力命令（非微信渠道专用） ---
    # 微信端收到该命令不触发（静默，防误触发）
    m_fuzzy = re.match(r"^#(?:推到|推送到|发到|同步到|发上|上|转到|转|弄到)?\s*微信\s*(接力|会话|同步)?\s*(.*)$", message)
    if m_fuzzy:
        if not is_weixin:
            # 非微信渠道：注册新会话到微信
            name = (m_fuzzy.group(2) or "").strip()
            name = re.sub(r"^(接力|会话|同步)", "", name).strip()
            if not name or len(name) < 2:
                name = "微信接力会话"
            await _handle_new_command(chat_id, message, name)
            _touch_heartbeat(f"HANDOFF #{name} from {platform}")
            return
        # 微信端：不触发（静默）
        _touch_heartbeat(f"HANDOFF-SKIP weixin-origin {message[:20]}")
        return

    # --- 以下管理命令仅微信渠道 ---
    if not is_weixin:
        return

    # --- #? 帮助命令（支持全角？） ---
    if re.match(r"^#[?？]\s*$", message):
        await _send_weixin_text(chat_id, _build_help_text())
        _touch_heartbeat("HELP #? sent")
        return

    # --- #0 目录命令 ---
    if re.match(r"^#0\b", message):
        catalog = _build_catalog_text()
        await _send_weixin_text(chat_id, catalog)
        _touch_heartbeat("CATALOG #0 sent")
        return

    # --- #删除 管理命令（同义词：删除/删掉/移除/取消/去掉/清除/销毁，必须带空格） ---
    m = re.match(r"^#(?:删除|删掉|移除|取消|去掉|清除|销毁)\s+([\d\s,，]+?)(?:\s+(-y))?\s*$", message)
    if m:
        await _handle_delete_command(chat_id, message, m)
        return

    # --- #新建 管理命令（同义词：新建/新增/创建/开/开启/建立，必须带空格） ---
    m = re.match(r"^#(?:新建|新增|创建|开|开启|建立)\s+(.+)$", message)
    if m:
        await _handle_new_command(chat_id, message, m.group(1).strip())
        return

    # --- #新建 无参数（自动命名） ---
    m = re.match(r"^#(?:新建|新增|创建|开|开启|建立)\s*$", message)
    if m:
        await _handle_new_command(chat_id, message, "")
        return

    # --- #N 多媒体 锚定命令（图片自动归属目标会话） ---
    m_anchor = re.match(r"^#(\d+)\s*(?:多媒体|发图|图片|截图|附件)\b\s*(.*)$", message)
    if m_anchor:
        num = int(m_anchor.group(1))
        registry = _load_registry()
        entry = _find_entry(registry, num) if registry else None
        if not entry:
            await _send_weixin_text(chat_id, f"⚠️ #{num} 不存在，无法锚定")
            return
        target_sid = entry.get("session_id") or ""
        if not target_sid:
            await _send_weixin_text(chat_id, f"⚠️ #{num} 是 cron 任务（无会话可锚定）")
            return
        note = m_anchor.group(2).strip()
        _write_anchor(chat_id, num, target_sid, note)
        await _send_weixin_text(
            chat_id,
            f"📌 已锚定 #{num}「{entry.get('name')}」\n"
            f"后续发送的图片/文件将自动归属该会话（发任意文字即解除锚定）",
        )
        _touch_heartbeat(f"ANCHOR #{num} {target_sid[:12]}")
        return

    # --- 图片消息检测（message 为空 + 锚定存在 → 回写目标会话） ---
    if not message.strip():
        anchor = _read_anchor(chat_id)
        if anchor:
            await _handle_anchored_media(chat_id, anchor)
            return

    # --- 任意文字消息 → 清除锚定 ---
    if message.strip():
        _clear_anchor(chat_id)


def _read_anchor(chat_id: str):
    """读取锚定状态。"""
    try:
        if not os.path.exists(ANCHOR_FILE):
            return None
        with open(ANCHOR_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("chat_id") == chat_id:
            return data
    except Exception:
        pass
    return None


def _write_anchor(chat_id: str, num: int, target_sid: str, note: str = "") -> None:
    """写入锚定状态。"""
    try:
        data = {
            "chat_id": chat_id,
            "target_num": num,
            "target_session_id": target_sid,
            "note": note,
            "set_at": datetime.now().isoformat(),
        }
        with open(ANCHOR_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[weixin-handoff] 锚定写入失败: {e}", flush=True)


def _clear_anchor(chat_id: str) -> None:
    """清除锚定（下一条文字消息触发）。"""
    try:
        anchor = _read_anchor(chat_id)
        if anchor:
            if os.path.exists(ANCHOR_FILE):
                os.remove(ANCHOR_FILE)
            _touch_heartbeat(f"ANCHOR-CLEAR {chat_id[:12]}")
    except Exception:
        pass


def _find_recent_media_files(window_sec: int = ANCHOR_IMAGE_WINDOW):
    """在图片缓存目录找最近 window_sec 秒内的新文件。"""
    import time as _time

    now = _time.time()
    found = []
    if not os.path.isdir(IMAGE_CACHE_DIR):
        return found
    for f in os.listdir(IMAGE_CACHE_DIR):
        fp = os.path.join(IMAGE_CACHE_DIR, f)
        if not os.path.isfile(fp):
            continue
        # 跳过非图片
        if not f.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
            continue
        mtime = os.path.getmtime(fp)
        if now - mtime <= window_sec:
            found.append((mtime, fp))
    found.sort(reverse=True)
    return [fp for _, fp in found]


async def _handle_anchored_media(chat_id: str, anchor: dict) -> None:
    """处理锚定期间的图片消息：找到新图片，回写到目标会话。"""
    target_sid = anchor.get("target_session_id") or ""
    target_num = anchor.get("target_num") or "?"
    if not target_sid:
        await _send_weixin_text(chat_id, "⚠️ 锚定会话无效，已解除")
        _clear_anchor(chat_id)
        return

    media_files = _find_recent_media_files()
    if not media_files:
        # 没找到新图片——可能是文件/语音等其他媒体
        await _send_weixin_text(
            chat_id,
            f"📎 收到媒体消息，但未在缓存中找到图片文件（可能是文件/语音）\n"
            f"已跳过回写（锚定保持）",
        )
        _touch_heartbeat(f"ANCHOR-MEDIA-NOFILE #{target_num}")
        return

    try:
        from hermes_state import SessionDB

        db = SessionDB()
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            for fp in media_files[:3]:  # 最多回写 3 张
                content = (
                    f"[The user sent an image~ 微信锚定 #{target_num} {now}]\n"
                    f"图片文件: {fp}"
                )
                db.append_message(
                    session_id=target_sid,
                    role="user",
                    content=content,
                    observed=True,
                )
            await _send_weixin_text(
                chat_id,
                f"📎 已发送 {len(media_files[:3])} 张图片到 #{target_num} 会话\n"
                f"（发任意文字解除锚定）",
            )
            _touch_heartbeat(f"ANCHOR-MEDIA #{target_num} x{len(media_files[:3])}")
        finally:
            db.close()
    except Exception as e:
        print(f"[weixin-handoff] 锚定媒体回写失败: {e}", flush=True)
        await _send_weixin_text(chat_id, f"❌ 图片回写失败: {e}")
        _touch_heartbeat(f"ERROR anchor-media:{e}")


def _latest_assistant_message(db, session_id):
    try:
        row = db._conn.execute(
            "SELECT content FROM messages "
            "WHERE session_id=? AND role='assistant' "
            "ORDER BY timestamp DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        return (row[0] or "") if row else ""
    except Exception as e:
        print(f"[weixin-handoff] 读取最新回复失败: {e}", flush=True)
        return ""


# 待确认删除的缓存（chat_id → {nums, expires}）
_pending_deletes = {}


async def _handle_delete_command(chat_id: str, raw: str, m) -> None:
    """处理 #删除 命令：列出待确认条目，-y 确认后删除。"""
    nums = [int(x) for x in re.split(r"[,，\s]+", m.group(1)) if x.strip()]
    confirmed = bool(m.group(2))

    registry = _load_registry()
    if not registry:
        await _send_weixin_text(chat_id, "❌ 注册表读取失败，请稍后再试")
        return

    # 过滤出存在的条目
    entries = []
    for n in nums:
        e = _find_entry(registry, n)
        if e:
            entries.append(e)
        else:
            await _send_weixin_text(chat_id, f"⚠️ #{n} 不存在，已跳过")

    if not entries:
        await _send_weixin_text(chat_id, "❌ 没有可删除的条目")
        return

    # 保护：#4 微信主会话禁止删除
    protected = [e for e in entries if e.get("num") == 4]
    if protected:
        await _send_weixin_text(
            chat_id, "🚫 #4 是微信主会话（当前对话），禁止删除"
        )
        entries = [e for e in entries if e.get("num") != 4]
        if not entries:
            return

    if not confirmed:
        # 列出待确认，等待 -y
        lines = ["🗑️ 确认删除以下条目？"]
        for e in entries:
            kind = "📅任务" if e.get("kind") == "cron" else "💬会话"
            lines.append(f"  #{e['num']} {kind} {e.get('name')}")
        lines.append("")
        lines.append("回复「#删除 编号 -y」确认执行（编号间用空格分隔）")
        text = "\n".join(lines)
        await _send_weixin_text(chat_id, text)
        _touch_heartbeat(f"DELETE-ASK #{[e['num'] for e in entries]}")
        return

    # 执行删除（物理删除：注册表 + state.db 会话/消息/子代理级联）
    nums_ok = [e["num"] for e in entries]
    deleted_names = []

    # 1. state.db 物理删除（仅 session 类型；cron 类型只有 note 无需删库）
    for e in entries:
        sid = e.get("session_id")
        if e.get("kind") == "session" and sid:
            try:
                from hermes_state import SessionDB

                db = SessionDB()
                try:
                    ok = db.delete_session(sid)
                    if ok:
                        deleted_names.append(f"#{e['num']} {e.get('name')}（库+目录已删）")
                    else:
                        deleted_names.append(f"#{e['num']} {e.get('name')}（库无记录，仅目录已删）")
                finally:
                    db.close()
            except Exception as exc:
                print(f"[weixin-handoff] 物理删除 {sid} 失败: {exc}", flush=True)
                deleted_names.append(f"#{e['num']} {e.get('name')}（⚠️库删除失败，仅目录已删）")
        else:
            deleted_names.append(f"#{e['num']} {e.get('name')}（cron 任务，仅目录已删）")

    # 2. 注册表移除
    registry["entries"] = [
        e for e in registry["entries"] if e.get("num") not in nums_ok
    ]
    registry["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00")
    try:
        with open(REGISTRY, "w", encoding="utf-8") as f:
            json.dump(registry, f, ensure_ascii=False, indent=2)
        lines = ["✅ 已删除（含桌面端）："]
        lines.extend(f"  {d}" for d in deleted_names)
        lines.append("")
        lines.append(f"剩余 {len(registry['entries'])} 条。发 #0 查看最新目录")
        await _send_weixin_text(chat_id, "\n".join(lines))
        _touch_heartbeat(f"DELETED #{nums_ok} (physical)")
    except Exception as e:
        print(f"[weixin-handoff] 删除失败: {e}", flush=True)
        await _send_weixin_text(chat_id, f"❌ 删除失败: {e}")
        _touch_heartbeat(f"ERROR delete:{e}")


def _next_free_num(registry) -> int:
    """取最小可用编号（循环利用释放的编号）。"""
    used = {e.get("num") for e in registry.get("entries", [])}
    n = 1
    while n in used:
        n += 1
    return n


async def _handle_new_command(chat_id: str, raw: str, name: str) -> None:
    """处理 #新建 命令：创建新会话并注册编号。"""
    import uuid as _uuid

    if not name:
        name = "微信新建会话"
    if len(name) > 40:
        name = name[:40]

    registry = _load_registry()
    if not registry:
        await _send_weixin_text(chat_id, "❌ 注册表读取失败，请稍后再试")
        return

    # 生成新 session_id（与 gateway 同格式）
    now = datetime.now()
    session_id = f"{now.strftime('%Y%m%d_%H%M%S')}_{_uuid.uuid4().hex[:8]}"

    # 在 state.db 创建会话记录（source=weixin-managed）
    try:
        from hermes_state import SessionDB

        db = SessionDB()
        try:
            db.create_session(session_id, source="weixin")
            # 写入初始占位消息，让会话有标题依据
            db.append_message(
                session_id=session_id,
                role="system",
                content=f"微信新建会话：{name}（通过 #新建 命令创建，可回复 #编号 续接）",
                observed=True,
            )
        finally:
            db.close()
    except Exception as e:
        print(f"[weixin-handoff] 创建会话失败: {e}", flush=True)
        await _send_weixin_text(chat_id, f"❌ 创建会话失败: {e}")
        _touch_heartbeat(f"ERROR new-session:{e}")
        return

    # 注册条目
    num = _next_free_num(registry)
    entry = {
        "num": num,
        "kind": "session",
        "name": name,
        "session_id": session_id,
        "note": f"weixin #新建 命令创建 · {now.strftime('%m-%d %H:%M')}",
    }
    registry["entries"].append(entry)
    registry["updated_at"] = now.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    try:
        with open(REGISTRY, "w", encoding="utf-8") as f:
            json.dump(registry, f, ensure_ascii=False, indent=2)
        await _send_weixin_text(
            chat_id,
            f"✅ 已新建会话「{name}」\n"
            f"  编号：#{num}\n"
            f"  会话ID：{session_id}\n\n"
            f"回复「#{num} 内容」即可向该会话发送消息",
        )
        _touch_heartbeat(f"NEW #{num} {name}")
    except Exception as e:
        print(f"[weixin-handoff] 注册新会话失败: {e}", flush=True)
        await _send_weixin_text(chat_id, f"❌ 注册失败: {e}")
        _touch_heartbeat(f"ERROR new-register:{e}")


async def handle(event_type, context):
    if event_type == "agent:start":
        await _handle_catalog_command(context)
        return
    if event_type != "agent:end":
        return

    platform = context.get("platform") or ""
    chat_id = context.get("chat_id") or ""
    # 强校验：仅真正来自微信的消息才回写
    if not _is_authorized_weixin(platform, chat_id):
        return

    message = (context.get("message") or "").strip()
    # 管理命令（#0/#?/#删除系/#新建系/#N 多媒体锚定 及同义词、#推到微信接力）在 agent:start 已直接响应，不回写
    if re.match(
        r"^(#0\b|#[?？]\s*$|#(?:删除|删掉|移除|取消|去掉|清除|销毁)\b|#(?:新建|新增|创建|开|开启|建立)\b|#\d+\s*(?:多媒体|发图|图片|截图|附件)\b|#(?:推到|推送到|发到|同步到|发上|上|转到|转|弄到)?\s*微信\s*(?:接力|会话|同步)?)",
        message,
    ):
        return
    # 图片消息（message 为空）由锚定逻辑处理，不回写
    if not message:
        return
    m = re.match(r"^#(\d+)", message)
    if not m:
        return
    num = int(m.group(1))
    if num == 0:
        # #0 目录命令已由 agent:start 直接响应，无需回写
        return

    weixin_session = context.get("session_id") or ""
    if not weixin_session:
        return

    registry = _load_registry()
    entry = _find_entry(registry, num) if registry else None
    if not entry:
        print(f"[weixin-handoff] #{num} 未在注册表中找到条目，跳过回写", flush=True)
        _touch_heartbeat(f"SKIP #{num} not-in-registry")
        return

    kind = entry.get("kind")
    name = entry.get("name") or "?"

    if kind == "session":
        target = entry.get("session_id")
        if not target or target == weixin_session:
            return
        try:
            from hermes_state import SessionDB

            db = SessionDB()
            try:
                reply = _latest_assistant_message(db, weixin_session) or ""
                now = datetime.now().strftime("%Y-%m-%d %H:%M")

                # 两条独立消息：用户提问 + 微信侧回复（真正的对话样式）
                user_text = message
                db.append_message(
                    session_id=target, role="user", content=user_text, observed=True
                )
                db.append_message(
                    session_id=target,
                    role="assistant",
                    content=reply[:MAX_REPLY_CHARS],
                    observed=True,
                )
                print(
                    f"[weixin-handoff] 已回写 #{num} → 会话 {target} "
                    f"(user[{len(user_text)}字符] + assistant[{min(len(reply), MAX_REPLY_CHARS)}字符])",
                    flush=True,
                )
                _touch_heartbeat(f"OK #{num} → {target}")
            finally:
                db.close()
        except Exception as e:
            print(f"[weixin-handoff] 回写失败: {e}", flush=True)
            _touch_heartbeat(f"ERROR writeback:{e}")

    elif kind == "cron":
        try:
            note = entry.get("note") or ""
            now = datetime.now().strftime("%m-%d %H:%M")
            entry["note"] = f"{note} | [{now}] 微信侧跟进: {message[:80]}"
            with open(REGISTRY, "w", encoding="utf-8") as f:
                json.dump(registry, f, ensure_ascii=False, indent=2)
            print(f"[weixin-handoff] 已更新 cron 条目 #{num} note", flush=True)
            _touch_heartbeat(f"OK #{num} cron-note")
        except Exception as e:
            print(f"[weixin-handoff] note 更新失败: {e}", flush=True)
            _touch_heartbeat(f"ERROR cron-note:{e}")
