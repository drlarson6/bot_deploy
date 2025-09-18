# router.py (keep only these)
from dataclasses import dataclass
from typing import Any, Callable, Dict, Tuple
from session_store import SessionStore
from actions import spreadsheet_registry  # wherever your registry lives
import re

@dataclass
class Ctx:
    session: Any
    SessionType: Any
    app: Any
    chat_with_gpt: Callable[[str], Tuple[str, Dict[str, Any]]]
    call_sheets_action: Callable[[str, Dict[str, Any]], Tuple[bool, str]]

def _pre_normalize(text: str) -> str:
    """
    Single, small normalization pass to fix common ASR confusions.
    Keep this list short and data-driven.
    """
    if not text:
        return ""
    t = text.lower().strip()

    # collapse multiple spaces/hyphens
    t = re.sub(r"[-_]+", " ", t)
    t = re.sub(r"\s+", " ", t)

    # high-impact mis-hears -> canonicalize to 'unbold'
    replacements = {
        "on bold": "unbold",
        "un bold": "unbold",
        "un- bold": "unbold",
        "un- bold header": "unbold header",
        "im bold": "unbold",
        "i'm bold": "unbold",
    }
    for k, v in replacements.items():
        t = t.replace(k, v)

    return t

_row_pat = re.compile(r"\brow\s+(\d+)\b", re.I)

def _extract_row(text: str) -> int:
    m = _row_pat.search(text or "")
    if m:
        try:
            return max(1, int(m.group(1)))
        except Exception:
            pass
    # common “first/top row” language
    if re.search(r"\b(first|1st|top)\s+row\b", text or "", re.I):
        return 1
    return 1  # default

def handle_session_text_router(user_text: str, ctx: Ctx):
    low = (user_text or "").strip().lower()
    if not ctx.session.mode:
        return False, None

    store = SessionStore(ctx.session)

    # --- enders ---
    if ctx.session.type == ctx.SessionType.DEVIATION and low == "close deviation":
        store.end_file()
        ctx.session.mode = False; ctx.session.type = ctx.SessionType.NONE; ctx.session.id = None
        return True, "Deviation session ended."

    if ctx.session.type == ctx.SessionType.SPREADSHEET and low in ("close spreadsheet","end spreadsheet","stop spreadsheet"):
        store.end_file()
        ctx.session.mode = False; ctx.session.type = ctx.SessionType.NONE; ctx.session.id = None
        return True, "Spreadsheet session ended."

    # --- route by type ---
    if ctx.session.type == ctx.SessionType.SPREADSHEET:
        # 0) normalize once (handles 'on bold' -> 'unbold', etc.)
        norm = _pre_normalize(user_text or "")
        row = _extract_row(norm)

        # 1) Intent: UNBOLD (check first so 'unbold' isn't swallowed by 'bold')
        if (
            ("unbold" in norm) or
            re.search(r"\bremove\s+bold\b", norm) or
            re.search(r"\bturn\s+off\s+bold\b", norm) or
            re.search(r"\bno\s+bold\b", norm) or
            re.search(r"\bmake\s+header\s+(normal|regular)\b", norm)
        ):
            ok, msg = ctx.call_sheets_action("UnboldHeader", {"row_number": row})
            reply = "Okay—done." if ok else f"Hmm—{msg}"
            ctx.app.logger.info(f"🎯 Voice→Sheets: UnboldHeader params={{'row_number': {row}}} ok={ok} reply={reply}")
            store.append_log(user_text, f"UnboldHeader(row={row}): {msg}")
            return True, reply

        # 2) Intent: BOLD
        if (
            re.search(r"\bbold\b", norm) or
            re.search(r"\bturn\s+on\s+bold\b", norm) or
            re.search(r"\bmake\s+header\s+bold\b", norm)
        ):
            ok, msg = ctx.call_sheets_action("BoldHeader", {"row_number": row})
            reply = "Okay—done." if ok else f"Hmm—{msg}"
            ctx.app.logger.info(f"🎯 Voice→Sheets: BoldHeader params={{'row_number': {row}}} ok={ok} reply={reply}")
            store.append_log(user_text, f"BoldHeader(row={row}): {msg}")
            return True, reply

        # 3) Fall back to your registry (kept intact), but pass normalized text first
        for act in spreadsheet_registry(ctx.call_sheets_action):
            # Try matching with normalized low text; pass original user_text for any rich parsing
            params = act.matches(norm, user_text)
            if params is not None:
                ok, reply = act.execute(params)
                ctx.app.logger.info(f"🎯 Voice→Sheets: {act.name} params={params} ok={ok} reply={reply}")
                store.append_log(user_text, f"{act.name}({params}): {reply}")
                return True, reply

        # 4) Spreadsheet GPT fallback
        reply, _ = ctx.chat_with_gpt(user_text)
        store.append_log(user_text, reply)
        return True, reply

    if ctx.session.type == ctx.SessionType.DEVIATION:
        reply, _ = ctx.chat_with_gpt(user_text)
        store.append_log(user_text, reply)
        return True, reply

    return False, None
