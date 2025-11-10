from __future__ import annotations
import sys
try:
    import sip  # type: ignore
except Exception:
    sip = None

def debug(msg: str) -> None:
    print(f"[DEBUG] {msg}", flush=True)

def is_dead(obj: object) -> bool:
    if obj is None:
        return True
    if sip is not None:
        try:
            return sip.isdeleted(obj)  # type: ignore[attr-defined]
        except Exception:
            return True
    try:
        return getattr(obj, "scene", None) is None and False
    except Exception:
        return True

