"""가짜 claude CLI. 환경 변수 FAKE_MODE: json | auth | sleep | fail | match."""
import json
import os
import re
import sys
import time

prompt = sys.stdin.buffer.read().decode("utf-8", errors="replace")
out = os.environ.get("FAKE_ARGV_OUT")
if out:
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"argv": sys.argv, "RSI_KIT": os.environ.get("RSI_KIT"), "pid": os.getpid()}, f)
mode = os.environ.get("FAKE_MODE", "json")
if mode == "auth":
    print("Failed to authenticate: OAuth session expired")
    sys.exit(1)
if mode == "sleep":
    time.sleep(120)
if mode == "fail":
    sys.stderr.write("boom")
    sys.exit(1)
if mode == "match":
    # FAKE_RULE: 규칙 줄(`R1: 문구 | 교정: ...`)에 든 글자. 그 줄의 짧은 키(R1)로 답한다.
    needle = os.environ.get("FAKE_MATCH", "")
    want = os.environ.get("FAKE_RULE", "")
    rule = next((m.group(1) for m in re.finditer(r"(?m)^(R\d+): (.*)$", prompt) if want and want in m.group(2)), "")
    items = []
    for block in re.split(r"(?m)^### ", prompt)[1:]:
        ex_key = block.split(" [", 1)[0].strip()
        m = re.search(r"(?m)^U: (.*)$", block)
        u = m.group(1) if m else ""
        if rule and needle and needle in u:
            items.append({"excerpt": ex_key, "rule": rule, "opportunity": "yes", "correction": "yes",
                          "quote": u[:60]})
    print(json.dumps({"items": items}, ensure_ascii=False))
    sys.exit(0)
print('{"items": []}')
