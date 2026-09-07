#!/opt/hermes/.venv/bin/python3
"""gateway 启动包装器：先应用微信限流补丁，再启动 gateway。

被 /run/service/gateway-default/run 调用（替代直接 hermes gateway run）。
补丁逻辑在 /opt/data/weixin_patch/sitecustomize.py 的 _apply_patch()。
"""
import sys

# 确保补丁目录可导入
sys.path.insert(0, "/opt/data/weixin_patch")

# 应用微信限流补丁（熔断期内等待冷却而非立即失败）
try:
    import weixin_patch_apply  # 纯函数补丁模块

    weixin_patch_apply.apply()
    print("[gateway-wrapper] 微信限流补丁已应用", flush=True)
except Exception as e:
    print(f"[gateway-wrapper] 补丁应用失败（不阻塞启动）: {e}", flush=True)

# 启动 gateway（main() 从 sys.argv 读参数，无位置参数）
from hermes_cli.main import main

sys.argv = ["hermes", "gateway", "run", "--replace"]
sys.exit(main())
