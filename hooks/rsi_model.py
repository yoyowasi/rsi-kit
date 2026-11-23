"""claude -p 한 번 호출. 도구 없음, stdin 입력. 로그인 만료(AuthError)와 일시 오류(TransientError)를 구분한다."""
import json
import os
import re
import shutil
import subprocess
import threading

import rsi_common

CREATE_NO_WINDOW = 0x08000000


class AuthError(RuntimeError):
    """CLI 로그인이 풀림 — 진행분을 남기고 이번 실행을 멈춘다."""


class TransientError(RuntimeError):
    """시간 초과·사용량 한도·그 밖의 0 아닌 종료 — 다음 실행에서 다시."""


class ModelTimeout(TransientError):
    """시간 초과. 묶음이 너무 커서 답이 늦을 수 있으므로 판정은 묶음을 나눠 다시 보낸다."""


# 아래 두 정규식은 second-brain-kit hooks/brain_common.py 의 _AUTH_HEAD, _AUTH_ANY 를 그대로 복사했다.
# CLI 자체 오류는 답의 맨 앞에 나온다. 본문에 '401' 이 들어 있어도 로그인 실패로 보지 않게 앞만 본다.
_AUTH_HEAD = re.compile(r"(?i)\s*(?:failed to authenticate|oauth (?:session|token)|not logged in|invalid api key|"
                        r"please run /login|api error:?\s*401|authentication_error)")
_AUTH_ANY = re.compile(r"(?i)failed to authenticate|oauth session expired|not logged in|please run /login|"
                       r"invalid api key|authentication_error|api error:?\s*401")


def _exe_prefix():
    raw = os.environ.get("RSI_CLAUDE_EXE", "").strip()
    if raw.startswith("["):
        try:
            prefix = json.loads(raw)
        except ValueError:
            raise TransientError("RSI_CLAUDE_EXE JSON 해석 실패")
        if not prefix or not all(isinstance(x, str) for x in prefix):
            raise TransientError("RSI_CLAUDE_EXE 가 비어 있거나 문자열 배열이 아님")
        return prefix
    exe = raw or shutil.which("claude")
    if not exe:
        raise TransientError("claude 실행 파일을 찾지 못함")
    return [exe]


def _kill_tree(p):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, timeout=30,
                           creationflags=CREATE_NO_WINDOW)
        else:
            import signal
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        p.kill()
    except OSError:
        pass


def _run(cmd, prompt, timeout, env):
    """subprocess.run 의 timeout 은 손자 프로세스가 파이프를 쥐면 돌아오지 않는다(Windows 의 claude.cmd).
    감시 타이머가 프로세스 트리째 끝내고, 그 뒤에도 파이프가 남으면 포기한다."""
    kw = {"creationflags": CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         env=env, **kw)
    expired = []

    def stop():
        expired.append(True)
        _kill_tree(p)

    watchdog = threading.Timer(timeout, stop)
    watchdog.daemon = True
    watchdog.start()
    try:
        out, err = p.communicate(prompt.encode("utf-8"), timeout=timeout + 2)
    except subprocess.TimeoutExpired:
        _kill_tree(p)
        raise
    finally:
        watchdog.cancel()
    if expired:
        raise subprocess.TimeoutExpired(cmd, timeout)
    return p.returncode, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")


def ask_model(system: str, prompt: str, model: str = "haiku", timeout: int = 240) -> str:
    cmd = _exe_prefix() + ["-p", "--model", model, "--setting-sources", "project", "--strict-mcp-config",
                           "--tools", "", "--no-session-persistence", "--safe-mode", "--output-format", "text",
                           "--system-prompt", system]
    env = dict(os.environ)
    env["RSI_KIT"] = "1"
    try:
        code, out, err = _run(cmd, prompt, timeout, env)
    except subprocess.TimeoutExpired:
        rsi_common.log("모델 호출 시간 초과(%d초)" % timeout)
        raise ModelTimeout("claude -p 시간 초과(%d초)" % timeout)
    except OSError as e:
        rsi_common.log("모델 실행 실패: %s" % e)
        raise TransientError("claude -p 실행 실패: %s" % e)
    out, err = out.strip(), err.strip()
    if (code == 0 and _AUTH_HEAD.match(out)) or (code != 0 and _AUTH_ANY.search("%s\n%s" % (out, err))):
        rsi_common.log("모델 로그인 오류: %s" % (out or err)[:200])
        raise AuthError((out or err)[:200])
    if code != 0:
        msg = (err or out)[:200]
        rsi_common.log("모델 호출 실패(exit %d): %s" % (code, msg))
        raise TransientError("claude -p 실패(exit %d): %s" % (code, msg))
    return out


def extract_json(answer: str) -> dict:
    """첫 '{' 부터 짝이 맞는 '}' 까지 json.loads. 문자열 안의 괄호는 세지 않는다."""
    start = answer.find("{")
    if start < 0:
        raise ValueError("JSON 객체가 없음")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(answer)):
        c = answer[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                obj = json.loads(answer[start:i + 1])
                if not isinstance(obj, dict):
                    raise ValueError("JSON 객체가 아님")
                return obj
    raise ValueError("짝이 맞는 '}' 가 없음")
