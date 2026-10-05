"""Shared helpers: robust JSON extraction and retrying LLM calls."""
import json
import time


def extract_objects(text: str) -> list[str]:
    objs, depth, start, in_str, esc = [], 0, None, False, False
    for i, ch in enumerate(text):
        if depth > 0 and in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"' and depth > 0:
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                objs.append(text[start:i + 1])
    return objs


def parse_json_object(text: str, required_key: str):
    """Last JSON object in `text` containing `required_key`, else None."""
    text = text.replace("```json", "").replace("```", "")
    for blob in reversed(extract_objects(text)):
        try:
            obj = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and required_key in obj:
            return obj
    return None


def text_of(msg) -> str:
    c = msg.content
    if isinstance(c, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in c)
    return c or ""


def is_transient(msg: str) -> bool:
    msg = msg.lower()
    return (("rate" in msg and "limit" in msg) or "429" in msg or "503" in msg
            or "output_parse_failed" in msg or "failed_generation" in msg)


def invoke_with_retries(agent, prompt: str, retries: int = 4):
    """Returns (result, retries_used). Backs off on rate limits and malformed output."""
    for attempt in range(retries):
        try:
            result = agent.invoke({"messages": [{"role": "user", "content": prompt}]},
                                  config={"recursion_limit": 30})   # step limit
            return result, attempt
        except Exception as exc:
            if is_transient(str(exc)) and attempt < retries - 1:
                print(f"    retry {attempt + 1}: {type(exc).__name__}")
                time.sleep(2 * 2 ** attempt)
                continue
            raise