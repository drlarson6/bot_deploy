import re
from dataclasses import dataclass
from typing import Callable, Optional, Dict, Any, Tuple

@dataclass
class Action:
    name: str
    matches: Callable[[str, str], Optional[Dict[str, Any]]]   # (low, orig) -> params|None
    execute: Callable[[Dict[str, Any]], Tuple[bool, str]]     # params -> (ok, reply)

def _extract_row(text: str) -> int:
    m = re.search(r"\brow\s+(\d+)\b", text, re.I)
    if m:
        try: return max(1, int(m.group(1)))
        except: pass
    if re.search(r"\b(first|1st|top)\s+row\b", text, re.I):
        return 1
    return 1

def match_bold_header(low: str, orig: str):
    if (("bold" in low) and ("header" in low or "headers" in low)) or re.search(r"\b(first|1st|top)\s+row\b.*\bbold\b", low, re.I):
        return {"row_number": _extract_row(orig)}
    return None

def match_unbold_header(low: str, orig: str):
    if ("unbold" in low and ("header" in low or "headers" in low)) or re.search(r"\bremove\b.*\bbold\b.*\bheader", low, re.I):
        return {"row_number": _extract_row(orig)}
    return None

def make_sheet_exec(call_sheets_action, action_name: str):
    def _exec(params):
        ok, msg = call_sheets_action(action_name, params)
        return ok, ("Okay—done." if ok else f"Hmm—{msg}")
    return _exec

def spreadsheet_registry(call_sheets_action):
    return [
        Action("BoldHeader",   match_bold_header,   make_sheet_exec(call_sheets_action, "BoldHeader")),
        Action("UnboldHeader", match_unbold_header, make_sheet_exec(call_sheets_action, "UnboldHeader")),
    ]
