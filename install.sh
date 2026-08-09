#!/usr/bin/env bash
# ============================================================
# 🏛️ Hermes 微驿 — 一键安装脚本
#
# 用法:
#   bash install.sh [HERMES_HOME]      # 指定 Hermes 数据目录（默认 $HOME/.hermes）
#
# 幂等：重复安装安全；不会覆盖已有注册表/配置。
# ============================================================
set -euo pipefail

HERMES_HOME="${1:-${HERMES_HOME:-$HOME/.hermes}}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "📦 Hermes 微驿 安装器"
echo "   目标目录: $HERMES_HOME"

# --- 1. Gateway Hook（核心：目录响应 / 回写 / 锚定 / 发送队列）---
HOOK_DIR="$HERMES_HOME/hooks/weixin-handoff"
mkdir -p "$HOOK_DIR"
cp "$REPO_DIR/hooks/weixin-handoff/HOOK.yaml" "$HOOK_DIR/"
cp "$REPO_DIR/hooks/weixin-handoff/handler.py" "$HOOK_DIR/"
echo "   ✅ Hook      → $HOOK_DIR"

# --- 2. Agent Skill（协议模板：#编号 处理流程）---
SKILL_DIR="$HERMES_HOME/skills/weixin-task-routing"
mkdir -p "$SKILL_DIR"
cp "$REPO_DIR/skills/weixin-task-routing/SKILL.md" "$SKILL_DIR/"
echo "   ✅ Skill     → $SKILL_DIR"

# --- 3. 辅助脚本（手动回写工具）---
SCRIPTS_DIR="$HERMES_HOME/scripts"
mkdir -p "$SCRIPTS_DIR"
cp "$REPO_DIR/scripts/weixin_handoff_append.py" "$SCRIPTS_DIR/"
echo "   ✅ 脚本      → $SCRIPTS_DIR/weixin_handoff_append.py"

# --- 4. 编号注册表（不覆盖已有）---
REGISTRY="$HERMES_HOME/weixin/task_registry.json"
if [ ! -f "$REGISTRY" ]; then
  mkdir -p "$HERMES_HOME/weixin"
  cat > "$REGISTRY" <<'EOF'
{
  "version": 2,
  "description": "Hermes 微驿编号注册表：cron 任务与会话接力统一编号。",
  "updated_at": "",
  "entries": []
}
EOF
  echo "   ✅ 注册表    → $REGISTRY（已初始化，空表）"
else
  echo "   ⏭️ 注册表    → 已存在，跳过（如需重置请手动删除）"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ 安装完成！"
echo ""
echo "下一步："
echo "  1. 重启 gateway：    hermes gateway restart"
echo "  2. 微信发送 #0 验证目录命令（应返回空目录或已有条目）"
echo "  3. 添加任务/会话：   微信发 #新建 名称，或编辑注册表"
echo ""
echo "卸载："
echo "  rm -rf $HOOK_DIR $SKILL_DIR $SCRIPTS_DIR/weixin_handoff_append.py"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
