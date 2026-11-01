"""Pull JSON out of an LLM reply, tolerating code fences and chatter around it."""
import json
import re

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)


class LLMParseError(ValueError):
    pass


def extract_json(text):
    if text is None:
        raise LLMParseError("Empty reply")
    text = str(text).strip()
    candidates = [m.group(1).strip() for m in _FENCE_RE.finditer(text)] + [text]
    decoder = json.JSONDecoder()
    for cand in candidates:
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            pass
        for i, ch in enumerate(cand):
            if ch in "[{":
                try:
                    obj, _ = decoder.raw_decode(cand[i:])
                    return obj
                except json.JSONDecodeError:
                    continue
    raise LLMParseError("No JSON found in Claude's reply")


def as_list_of_dicts(obj, key_hint=None):
    """Accept [..], {"jobs": [..]}, {"results": [..]} or a single {..}."""
    if isinstance(obj, list):
        return [o for o in obj if isinstance(o, dict)]
    if isinstance(obj, dict):
        for k in ([key_hint] if key_hint else []) + ["results", "jobs", "items", "drafts", "messages", "data"]:
            if k and isinstance(obj.get(k), list):
                return [o for o in obj[k] if isinstance(o, dict)]
        return [obj]
    raise LLMParseError("JSON was not an object or list")
