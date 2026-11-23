# rsi-kit

채택한 Claude Code 규칙이 반복 교정을 실제로 막는지 재는 독립 Python 도구입니다. 표준 라이브러리만 쓰고 Python 3.9 이상에서 동작합니다.

## 설치

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File install.ps1 [-ClaudeDir <폴더>]
```

```bash
# macOS / Linux / Git Bash
bash install.sh [--claude-dir <폴더>]
```

`hooks/rsi*.py`를 `~/.claude/hooks/`로 복사하고 `settings.json`에 hook 두 그룹을 등록합니다. 여러 번 실행해도 같고, 바뀔 때만 `settings.json.bak-날짜시각`을 남깁니다. 다른 hook 그룹은 건드리지 않습니다. `settings.json` 모양이 예상과 다르면 고치지 않고 멈춥니다.

등록한 hook 명령은 `$HOME/.claude/hooks`를 가리킵니다. 다른 폴더(`-ClaudeDir`/`--claude-dir`, `CLAUDE_CONFIG_DIR`)를 쓰면 설치가 경고를 내니 `settings.json`의 경로를 그 폴더로 고치세요.

## 첫 실행

```powershell
py -3 "$HOME\.claude\hooks\rsi.py" init
```

```bash
python3 ~/.claude/hooks/rsi.py init
```

1. `init`이 `~/.rsi-kit/spec/rules.draft.json`을 만듭니다.
2. 내용을 검토하고 파일 이름을 `rules.json`으로 바꿉니다.
3. `py -3 "$HOME\.claude\hooks\rsi.py" judge`(bash는 `python3 ~/.claude/hooks/rsi.py judge`)로 판정합니다(이후 `status`로 상태 확인).

## 제거

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1 -Remove
```

```bash
bash install.sh --remove
```

`settings.json`의 rsi 등록만 뺍니다. `~/.claude/hooks/rsi*.py`와 `~/.rsi-kit`은 지우지 않으니 필요하면 직접 지웁니다.

## 무엇을 바꾸는지

- `~/.claude/settings.json`: SessionStart 그룹(`rsi.py hook-session-start`)과 PreToolUse 그룹(`rsi-guard.py ... || true`, 매처 `Edit|Write|MultiEdit|NotebookEdit|Bash|PowerShell`) 두 개. 잠금 명령 끝의 `|| true`는 hook 파일이 없어도 도구 호출을 막지 않게 합니다(막을 때는 잠금이 deny 응답을 냅니다).
- `~/.claude/hooks/`: `rsi*.py` 파일들
- `~/.rsi-kit/`: 키트가 첫 실행 때 만드는 상태 폴더(설치는 만들지 않음)
- 볼트 `00_Inbox/`: `YYYY-MM-DD dream 규칙 효과 판정.md` 판정 파일

## 한계

- 잠금(guard)은 셸을 통한 우회를 완전히 막지 못합니다.
- 판정은 haiku 모델을 호출하므로 사용자 계정의 사용량을 씁니다.
- 혼자 보내도 답이 깨지는 발췌는 `판정 실패`로 건너뜁니다(`status`에 수가 나옵니다). 다시 판정하려면 `~/.rsi-kit/judgments.jsonl`에서 그 발췌의 `"rule": "!"` 줄을 지우거나, `~/.rsi-kit/spec/judge-prompt.md`를 고칩니다(이때는 모든 발췌를 다시 판정합니다).
