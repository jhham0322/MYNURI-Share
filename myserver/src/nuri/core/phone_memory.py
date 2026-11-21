"""Extract a concrete statement marked for local memory."""
import re

_EXPLICIT = re.compile(r"^(?:기억해\s*줘|기억할\s*내용|중요한\s*내용|remember)\s*[:：]\s*(.+)$", re.I)
_SUFFIX = re.compile(r"^(.+?)\s*[,，]?\s*(?:기억해\s*줘|기억해\s*주세요|잊지\s*말아\s*줘)[.!]?$")


def selected_memory(command):
    """Return only the concrete statement marked for memory."""
    match = _EXPLICIT.fullmatch(command.strip()) or _SUFFIX.fullmatch(command.strip())
    if match:
        return match[1].strip()[:500]
    return ""
