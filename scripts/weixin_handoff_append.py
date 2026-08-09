#!/usr/bin/env python3
"""
weixin_handoff_append.py — 把微信侧的对话记录追加到指定 Hermes 会话的 transcript。

用法:
    python3 weixin_handoff_append.py <session_id> <文本>        # 文本作为参数
    echo "文本" | python3 weixin_handoff_append.py <session_id>  # 文本从 stdin
    python3 weixin_handoff_append.py <session_id> --preview     # 只打印将要写入的内容

设计:
- 用官方 SessionDB.append_message(session_id, role="user", ...) 写入 state.db。
- role 固定 "user"（mirror 语义）：微信侧的对话以"用户消息"身份进入目标会话，
  桌面 agent 下次回复时能看到这段记录，且不会破坏 assistant→assistant 交替。
- 调用方（微信 agent）负责在文本中带上下文标记，如:
  "[微信接力 2026-08-05 12:30] 用户问：#3 继续 → 已回复：xxx"
- 失败时 exit 非 0 并打印错误到 stderr，方便调用方感知。
"""
import sys
import os

# hermes_state 位于 /opt/hermes（与 gateway 同目录）
sys.path.insert(0, "/opt/hermes")

def find_session(db, session_id: str):
    """确认会话存在，返回其 source/title（用于校验，避免写错目标）。"""
    try:
        row = db._conn.execute(
            "SELECT id, source, title FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return row
    except Exception:
        return None

def main() -> int:
    args = sys.argv[1:]
    if not args:
        print("用法: weixin_handoff_append.py <session_id> <文本|-> [--preview]", file=sys.stderr)
        return 2

    session_id = args[0]
    rest = args[1:]
    preview = "--preview" in rest
    rest = [a for a in rest if a != "--preview"]

    if rest:
        text = rest[0]
    else:
        text = sys.stdin.read().strip()

    if not text:
        print("错误: 文本为空", file=sys.stderr)
        return 2

    # 格式前缀（调用方可自行决定是否保留；这里确保最小编号格式提示）
    if not text.startswith("[微信接力") and not text.startswith("【微信接力"):
        text = f"[微信接力] {text}"

    try:
        from hermes_state import SessionDB
        db = SessionDB()
    except Exception as e:
        print(f"错误: 无法初始化 SessionDB: {e}", file=sys.stderr)
        return 1

    try:
        row = find_session(db, session_id)
        if row is None:
            print(f"错误: 会话不存在: {session_id}", file=sys.stderr)
            return 1
        source = str(row[1] or "?")
        title = str(row[2] or "?")

        if preview:
            print(f"[preview] 将追加到会话 {session_id} (source={source}, title={title})")
            print("--- 内容 ---")
            print(text)
            print("------------")
            return 0

        msg_id = db.append_message(
            session_id=session_id,
            role="user",
            content=text,
            observed=True,
        )
        print(f"OK: 已追加到会话 {session_id} (source={source}, title={title}), msg_id={msg_id}")
        return 0
    except Exception as e:
        print(f"错误: 追加失败: {e}", file=sys.stderr)
        return 1
    finally:
        try:
            db.close()
        except Exception:
            pass

if __name__ == "__main__":
    sys.exit(main())
