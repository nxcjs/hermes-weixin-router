"""weixin_patch_apply — 微信限流修复补丁（纯函数版，供 gateway_launcher 调用）。

背景（2026-08-16）：
weixin.py 的 _send_text_chunk_locked 有两处会让消息直接失败：
  1. 熔断期内进入时 `raise self._rate_limit_error()`
  2. 服务器限流(-2)触发熔断后 break + raise
导致 agent 回复失败入投递账本、要等 gateway 重启才补发（排队形同虚设）。

补丁完整替换该方法：两处"熔断失败"都改为"等待冷却结束(+2s 余量)再重试"。
配合 _send_text_gate 全局门闩，积压消息冷却后逐个排空，不扎堆、不丢消息。

使用：gateway_launcher.py 在启动 gateway 前调用 apply()。
"""

import logging

_log = logging.getLogger("gateway.platforms.weixin")
_PATCHED_ATTR = "_cooldown_wait_patched"


def apply() -> bool:
    """对 weixin.WeixinAdapter._send_text_chunk_locked 打补丁。幂等。"""
    try:
        from gateway.platforms import weixin
    except Exception:
        # gateway 主流程会导入 weixin；若此处太早，注册 import hook 兜底
        import builtins as _b

        _orig = _b.__import__

        def _hook(name, *args, **kwargs):
            mod = _orig(name, *args, **kwargs)
            if name == "gateway.platforms.weixin":
                try:
                    apply()
                except Exception:
                    pass
            return mod

        _b.__import__ = _hook
        return False

    adapter_cls = weixin.WeixinAdapter
    if getattr(adapter_cls, _PATCHED_ATTR, False):
        return True  # 已打过，幂等

    async def _send_text_chunk_locked_patched(
        self, *, chat_id, chunk, context_token, client_id
    ):
        """熔断期内等待冷却结束再重试，不立即失败入账本（2026-08-16 补丁）。"""
        import asyncio
        import os
        from gateway.platforms.weixin import _safe_id as _ws_safe_id

        max_wait = float(os.getenv("WEIXIN_RATE_LIMIT_MAX_WAIT_SECONDS", "120.0"))

        async def _wait_out_cooldown(reason: str) -> bool:
            """等待熔断结束。True=已等待；False=超过上限需放弃。"""
            cooldown = self._rate_limit_cooldown_remaining()
            if cooldown <= 0:
                return True
            wait = cooldown + 2.0
            if wait > max_wait:
                return False
            _log.warning(
                "[%s] %s for %s; waiting %.1fs before retry",
                self.name, reason, _ws_safe_id(chat_id), wait,
            )
            await asyncio.sleep(wait)
            return True

        last_error = None
        retried_without_token = False
        for attempt in range(self._send_chunk_retries + 1):
            # 修复点 1：熔断期内等待冷却，而非立即失败
            if not await _wait_out_cooldown("rate limit circuit open"):
                raise self._rate_limit_error()
            try:
                from gateway.platforms.weixin import _send_message

                resp = await _send_message(
                    self._send_session,
                    base_url=self._base_url,
                    token=self._token,
                    to=chat_id,
                    text=chunk,
                    context_token=context_token,
                    client_id=client_id,
                )
                if resp and isinstance(resp, dict):
                    ret = resp.get("ret")
                    errcode = resp.get("errcode")
                    if (ret is not None and ret not in {0,}) or (
                        errcode is not None and errcode not in {0,}
                    ):
                        is_session_expired = (
                            ret == weixin.SESSION_EXPIRED_ERRCODE
                            or errcode == weixin.SESSION_EXPIRED_ERRCODE
                            or weixin._is_stale_session_ret(
                                ret, errcode, resp.get("errmsg")
                            )
                        )
                        if (
                            is_session_expired
                            and not retried_without_token
                            and context_token
                        ):
                            retried_without_token = True
                            context_token = None
                            self._token_store._cache.pop(
                                self._token_store._key(self._account_id, chat_id), None
                            )
                            _log.warning(
                                "[%s] session expired for %s; retrying without context_token",
                                self.name, _ws_safe_id(chat_id),
                            )
                            continue
                        is_rate_limited = (
                            ret == weixin.RATE_LIMIT_ERRCODE
                            or errcode == weixin.RATE_LIMIT_ERRCODE
                        )
                        if is_rate_limited:
                            errmsg = resp.get("errmsg") or resp.get("msg") or "rate limited"
                            last_error = RuntimeError(
                                f"iLink sendmessage rate limited: ret={ret} "
                                f"errcode={errcode} errmsg={errmsg}"
                            )
                            if self._record_rate_limit_event():
                                # 修复点 2：熔断已开 → 等待冷却后重试，而非 break
                                if not await _wait_out_cooldown(
                                    "rate limited (circuit opened)"
                                ):
                                    raise self._rate_limit_error()
                                continue
                            if attempt >= self._send_chunk_retries:
                                break
                            wait = self._send_chunk_retry_delay_seconds * 3
                            _log.warning(
                                "[%s] rate limited for %s; backing off %.1fs before retry",
                                self.name, _ws_safe_id(chat_id), wait,
                            )
                            await asyncio.sleep(wait)
                            continue
                        errmsg = resp.get("errmsg") or resp.get("msg") or "unknown error"
                        raise RuntimeError(
                            f"iLink sendmessage error: ret={ret} errcode={errcode} errmsg={errmsg}"
                        )
                self._reset_rate_limit_circuit()
                return
            except Exception as exc:
                last_error = exc
                if attempt >= self._send_chunk_retries:
                    break
                wait = self._send_chunk_retry_delay_seconds * (attempt + 1)
                _log.warning(
                    "[%s] send chunk failed to=%s attempt=%d/%d, retrying in %.2fs: %s",
                    self.name,
                    _ws_safe_id(chat_id),
                    attempt + 1,
                    self._send_chunk_retries + 1,
                    wait,
                    exc,
                )
                if wait > 0:
                    await asyncio.sleep(wait)
        assert last_error is not None
        raise last_error

    _send_text_chunk_locked_patched.__name__ = "_send_text_chunk_locked_patched"
    adapter_cls._send_text_chunk_locked = _send_text_chunk_locked_patched
    adapter_cls._cooldown_wait_patched = True
    _log.info("[Weixin] cooldown-wait patch applied: circuit-open messages wait & retry")
    return True


if __name__ == "__main__":
    ok = apply()
    from gateway.platforms import weixin

    print(
        "补丁应用:",
        ok,
        "| 方法名:",
        weixin.WeixinAdapter._send_text_chunk_locked.__name__,
    )
