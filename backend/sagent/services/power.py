"""AI 工作跑的時候讓電腦保持喚醒。

CPU 上一份文件要跑幾十分鐘，使用者常常開了就離開。這台筆電是新式待命（S0 低電源閒置）：螢幕因為閒置
關掉時就會進入待命，程式整個被暫停，等人回來喚醒才繼續。2026-11-04 晚上的可靠度評估，有兩份因此從
16 分鐘拖到 280 分鐘以上（系統事件 506、507）。

做法是 Windows 的 power request（PowerCreateRequest / PowerSetRequest）：
- 同時要求「系統保持執行」和「螢幕保持亮著」。新式待命在螢幕關掉時就待命，只要求系統不夠。
- 要求跟著 handle，不綁執行緒；系統管理員用 `powercfg /requests` 看得到原因「SAgent：AI 工作執行中」。
- 闔上螢幕、按電源鍵還是會待命，那是使用者自己要的。

不是 Windows、或呼叫失敗時什麼都不做（只記 log），不能因為這個讓 AI 工作失敗。
設定 SAGENT_KEEP_AWAKE=0 可以關掉。
"""

from __future__ import annotations

import logging
import sys
from typing import Any

log = logging.getLogger("sagent.power")

REASON = "SAgent：AI 工作執行中"
_DISPLAY_REQUIRED = 0  # POWER_REQUEST_TYPE
_SYSTEM_REQUIRED = 1


class _WindowsApi:
    """kernel32 的 power request。只在 Windows 上建立。"""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class _Detailed(ctypes.Structure):
            _fields_ = [
                ("LocalizedReasonModule", wintypes.HMODULE),
                ("LocalizedReasonId", wintypes.ULONG),
                ("ReasonStringCount", wintypes.ULONG),
                ("ReasonStrings", ctypes.POINTER(wintypes.LPWSTR)),
            ]

        class _Reason(ctypes.Union):
            _fields_ = [("Detailed", _Detailed), ("SimpleReasonString", wintypes.LPWSTR)]

        class _ReasonContext(ctypes.Structure):
            _fields_ = [("Version", wintypes.ULONG), ("Flags", wintypes.DWORD), ("Reason", _Reason)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.PowerCreateRequest.restype = wintypes.HANDLE
        k32.PowerCreateRequest.argtypes = [ctypes.POINTER(_ReasonContext)]
        k32.PowerSetRequest.restype = wintypes.BOOL
        k32.PowerSetRequest.argtypes = [wintypes.HANDLE, ctypes.c_int]
        k32.PowerClearRequest.restype = wintypes.BOOL
        k32.PowerClearRequest.argtypes = [wintypes.HANDLE, ctypes.c_int]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._k32, self._ctypes, self._context = k32, ctypes, _ReasonContext

    def hold(self, reason: str) -> Any:
        ctx = self._context(0, 1)  # POWER_REQUEST_CONTEXT_VERSION、POWER_REQUEST_CONTEXT_SIMPLE_STRING
        ctx.Reason.SimpleReasonString = reason
        handle = self._k32.PowerCreateRequest(self._ctypes.byref(ctx))
        if not handle or handle == -1:
            raise OSError(self._ctypes.get_last_error(), "PowerCreateRequest 失敗")
        for kind in (_SYSTEM_REQUIRED, _DISPLAY_REQUIRED):
            if not self._k32.PowerSetRequest(handle, kind):
                err = self._ctypes.get_last_error()
                self._k32.CloseHandle(handle)
                raise OSError(err, "PowerSetRequest 失敗")
        return handle

    def release(self, handle: Any) -> None:
        for kind in (_SYSTEM_REQUIRED, _DISPLAY_REQUIRED):
            self._k32.PowerClearRequest(handle, kind)
        self._k32.CloseHandle(handle)


class KeepAwake:
    """hold() 之後電腦不會因為閒置待命，release() 放開。可以重複 hold，最後一次 release 才放開。"""

    def __init__(self, enabled: bool = True, api: Any = None):
        self.enabled = enabled
        self._api = api
        self._handle: Any = None
        self._count = 0

    def _get_api(self) -> Any:
        if self._api is None and sys.platform == "win32":
            self._api = _WindowsApi()
        return self._api

    @property
    def active(self) -> bool:
        return self._handle is not None

    def hold(self) -> None:
        self._count += 1
        if not self.enabled or self._handle is not None:
            return
        try:
            api = self._get_api()
            if api is not None:
                self._handle = api.hold(REASON)
        except Exception:  # 保持喚醒失敗不影響 AI 工作
            log.warning("無法讓電腦保持喚醒，AI 工作照常執行，但電腦可能會進入待命", exc_info=True)

    def release(self) -> None:
        self._count = max(self._count - 1, 0)
        if self._count or self._handle is None:
            return
        handle, self._handle = self._handle, None
        try:
            self._api.release(handle)
        except Exception:
            log.warning("放開保持喚醒的要求失敗", exc_info=True)

    def __enter__(self) -> KeepAwake:
        self.hold()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
