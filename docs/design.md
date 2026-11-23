# RSI 킷 v1 설계: 채택한 규칙이 먹히는지 판정하기

작성 2026-11-23 · 상태: 초안(사용자 승인 대기) · 검토자(builder check) 1회 반영 · 저장소를 만들면 `docs/`로 옮긴다.

## 1. 왜 만드나

지금 dream 흐름(대화 → dream 규칙 제안 → `/inbox`에서 사용자 승인 → 전역 CLAUDE.md `[Lnn]` 또는 프로젝트 메모리)은 "제한된 자기 개선" 루프다. 그런데 **채택한 규칙이 실제로 같은 교정을 줄였는지 재는 장치가 없다**(볼트 `40_Resources/재귀적 자기 개선(RSI) 엔지니어링.md` 6절).

- **사용자 목적(2026-11-23):** 안 먹히는 규칙을 찾아서 **빼거나 hook으로 강제**한다.
- **RSI 핵심 원칙:** 고치는 쪽(dream·규칙)과 채점하는 쪽(이 킷)을 떼어 놓고, 채점 기준은 사람이 소유한다. 채점까지 고칠 수 있으면 점수를 믿을 수 없다(같은 노트 3·4절, DGM 평가 조작 사례). 그래서 **이 킷은 판정만 하고 규칙 문구를 새로 쓰지 않는다.**
- 이 킷은 RSI 킷의 첫 부품, 곧 **평가자**다. 나중에 스킬·orchestrate 같은 다른 표면으로 넓힌다.

## 2. 목표 · 비목표 · 반목표

**목표 (v1)**
1. 채택 규칙마다, 그 문구가 적용된 날 뒤의 대화를 보고 **재발 / 관찰 / 지켜짐 / 판단 불가**로 판정한다.
2. 재발한 규칙에 추천을 붙인다: 강제 수단이 있는 예외 없는 규칙은 **hook 후보**, 나머지는 **빼기 제안**.
3. 결과를 볼트 인박스에 써서 지금처럼 `/inbox`에서 사용자가 승인한다.
4. 대화 발췌를 Claude Code의 30일 삭제와 상관없이 자체 보관한다.
5. 채점 모델의 판정을 사람이 확인하는 보정 단계를 둔다.

**비목표 (v1에서 안 함)**
- hook 코드를 자동으로 쓰거나 `settings.json`에 등록하기(승인 뒤 별도 작업).
- CLAUDE.md·메모리를 자동으로 고치기(지금처럼 `/inbox`가 한다).
- 규칙의 새 문구 만들기(고쳐 남기고 싶으면 `/inbox`의 기존 "문구 수정" 답으로 한다).
- 스킬·orchestrate·하네스 코드 같은 다른 개선 표면.
- Codex 대화(dream도 Claude Code 대화만 본다).
- 채택 전과 후 비교(대화 원본이 2026-11-11 이후 것만 남아 대부분 규칙은 채택 전 자료가 없다).
- Claude 자체 auto memory 효과와 규칙 효과를 떼어 보기.
- 만든 hook 자체의 효과 측정(v2 후보).

**반목표 (이렇게 되면 실패)**
- 킷이 규칙이나 **자기 채점 기준을 스스로 바꾼다.**
- "판단 불가" 규칙을 빼기로 추천한다(멀쩡한 규칙을 잃는다).
- 사용자가 이미 정한 제안을 매주 다시 올린다([L07]과 충돌), 인박스가 판정 파일로 넘친다.
- 대화 원문이 볼트(git·클라우드로 동기화됨)에 들어간다.

## 3. 제약

- **대화 원본:** `~/.claude/projects/*/*.jsonl`. `cleanupPeriodDays` 기본 30일 뒤 삭제(현재 설정 없음 = 기본값). 2026-11-23 기준 가장 오래된 기록은 2026-11-11, 311개.
- **규칙 현황:** 전역 `[L01]`~`[L21]` 중 20개(`[L10]`은 2026-10-30 삭제), `source: dream` 프로젝트 메모리 4개. CLAUDE.md 표식 `<!-- brain:start -->`·`<!-- brain:end -->`, `<!-- dream-last-id: 21 -->`.
- **모델 호출:** dream과 같은 방식 `claude -p --model haiku` (사용자 계정, 터미널 CLI 로그인 필요). 재귀 방지 환경 변수, `--no-session-persistence`, `--safe-mode`, 도구 없음(`brain_common.ask_model` 참고).
- **지금 로그인이 풀려 있다(기존 문제, 이 설계와 무관):** `~/.claude/hooks/brain-dream.log`에 2026-11-23 "OAuth session expired"가 반복되고 dream이 건너뛰어지며 일지 대기 20건이 쌓여 있다. 사용자가 터미널에서 `claude auth login`을 하기 전에는 이 킷의 판정도 돌지 않는다.
- **승인 경계:** 규칙 변경과 `settings.json` 변경은 사용자 승인(볼트 `AGENTS.md` "자동 기록과 승인", 판단 방식 5·10번). 커밋·push는 요청할 때만.
- **환경:** Windows 우선(PowerShell 설치), macOS/Linux도 동작. Python 3.9+ 표준 라이브러리만. second-brain-kit와 같은 모양(hooks/, install.ps1·install.sh, tests/).
- **비밀정보:** 저장 전과 모델에 보내기 전에 가린다(dream의 `scrub`과 같은 패턴).

## 4. 방식 (Approaches)

| 방식 | 좋은 점 | 아쉬운 점 |
|---|---|---|
| A. second-brain-kit의 dream 옆에 붙이기 | 대화 읽기·모델 호출·인박스 쓰기를 그대로 씀, 설치 하나 | 규칙 제안과 채점이 같은 코드에 있어 평가 분리가 약함 |
| **B. 새 rsi-kit 저장소, 파일 약속으로만 연결 (선택)** | 채점 코드·기준을 개선 대상과 떼어 잠글 수 있음, 다른 표면으로 넓히기 쉬움 | 대화 읽는 코드를 따로 둠(작음), `/inbox`에 한 줄 추가 필요 |
| C. 부를 때만 도는 스킬 하나 | 가장 빨리 만듦 | 채점자가 그때그때 세션 모델이라 고정 평가자가 아님, 기록이 안 쌓임, 30일 삭제 문제 그대로 |

**사용자가 B를 골랐다(2026-11-23).** 이유: 채점하는 쪽이 개선 대상과 떨어져 있어야 측정을 믿을 수 있다. A로 아끼는 것은 대화 읽기 정도로 작다.

## 5. 설계

### 5.1 구성과 위치

- **저장소:** `C:\dev\rsi-kit`(새로 만든다).
- **설치본:** `~/.claude/hooks/rsi-*.py`(second-brain hook 옆), 실행 기록 `~/.claude/hooks/rsi.log`.
- **상태 폴더:** `~/.rsi-kit/` (볼트 밖, 동기화 안 됨)
  - `spec/` — **사람 소유, 에이전트 쓰기 금지**(5.7): `rules.json`(판정 기준), `judge-prompt.md`(채점 프롬프트), `thresholds.json`(기준값), `review/`(보정 확인 파일, 5.5)
  - `registry.json` — 규칙 사실 목록(킷이 관리, 5.2)
  - `excerpts/YYYY-MM.jsonl` — 대화 발췌
  - `verdicts/YYYY-MM-DD.json` — 판정 기록(추세용)
  - `calibration.jsonl` — 사람 확인 결과 누적
  - `state.json` — 마지막 수집·판정 시각, 이미 낸 제안과 그 결정
- **읽기만 하는 입력(파일 약속):**
  - 대화: `~/.claude/projects/*/*.jsonl`
  - 규칙: `~/.claude/CLAUDE.md`의 `<!-- brain:start -->`~`<!-- brain:end -->` 안 `- [Lnn] ` 줄, `~/.claude/projects/*/memory/*.md` 중 `source: dream`
  - 채택 기록: 볼트 `90_Archive/`·`00_Inbox/`의 `type: dream` 파일(번호 제안의 `- 규칙:` 줄과 `## 처리 결과 (YYYY-MM-DD)` 절)
  - 볼트 경로: second-brain과 같은 순서 `BRAIN_DIR` > `SECOND_BRAIN` 환경 변수 > `~/.second-brain/config.json`의 `vault`
- **쓰는 곳:** `~/.rsi-kit/`, 그리고 쓸 것이 있을 때만 판정 1회당 볼트 `00_Inbox/` 파일 1개(5.6).
- **다른 킷 변경은 한 곳뿐:** second-brain-kit `skills/inbox/SKILL.md` 4절에 "RSI 판정 파일의 `## hook 후보`·`## 확인할 판정` 절은 보여 주기만 하고 규칙을 고치지 않는다" 한 줄.

### 5.2 규칙: 사실은 킷이, 판정 기준은 사람이

규칙 정보를 둘로 나눈다. **사실(문구, 날짜)은 CLAUDE.md에서 저절로 따라오고, 판단(유형, 강제 수단)만 사람이 잠근다.** 그래야 `/inbox`가 문구를 고쳐도 사람이 잠긴 파일을 손볼 일이 없다.

**`registry.json` (킷이 관리하는 사실)**

| 필드 | 뜻 |
|---|---|
| `id` | `L06` 또는 메모리 `<slug>/<file>.md` |
| `text` | 지금 문구 |
| `adopted` | 채택일 |
| `text_since` | 지금 문구가 적용된 날(채택일 또는 마지막 문구 변경일) |
| `origin` | 처음 교정 근거 인용(dream 제안의 `근거:`) |
| `removed` | 규칙이 사라진 날(없으면 null) |

- **처음 만들기(`rsi init`):** 채택 기록에서 채택일을 찾는다. 처리 결과에는 `반영: 1, 2, 3`처럼 **번호만 적힌 경우가 많아**(예: 2026-11-03~11-11 파일) 그 번호의 `- 규칙:` 문구와 지금 CLAUDE.md 줄을 비슷한 정도로 맞춘다. `[Lnn]`이 직접 적힌 경우(`1 → [L01]`, `1 → 전역 [L09]`, `반영: 1 (전역 CLAUDE.md [L21])`)는 그대로 쓴다. 맞추지 못하거나 애매한 것은 초안에 "확인 필요"로 남기고 **사용자가 한 번 확인한다.**
- **매일 갱신(수집 때):** CLAUDE.md의 규칙 줄을 읽어 문구가 바뀌면 `text`·`text_since`(그날)를 고치고, 새 `[Lnn]`이 생기면 항목을 더하고(`adopted` = 그 처리 결과 날짜, 못 찾으면 처음 본 날), 사라지면 `removed`를 적는다. 사실만 따라가므로 사람 승인이 필요 없다.

**`spec/rules.json` (사람 소유 판정 기준, 잠금)**

| 필드 | 뜻 |
|---|---|
| `id` | registry와 같은 id |
| `type` | `step`(단계 추가: 보고에 X 넣기 등) / `restraint`(자제: ~하지 않기) / `preference`(선호) |
| `no_exceptions` | 예외 없이 지켜야 하나(true/false) |
| `enforce` | 결정적으로 강제할 수단: `pretooluse`(도구 실행 전 차단) / `stop`(마무리 직전 보고 검사) / `reminder`(말 받을 때 상기) / `none` |
| `opportunity` | `{"kind": "tool", "tools": [...], "pattern": "정규식"}` 또는 `{"kind": "llm", "when": "적용 상황 한 줄"}` |
| `correction` | 어떤 사용자 말이 이 규칙 위반을 교정한 것인지 한 줄 |
| `enforced_by` | 이미 만든 hook 이름(없으면 null). 있으면 교정 기반 제안을 내지 않는다(효과 측정은 v2) |

- `rsi init`이 `spec/rules.draft.json` 초안을 쓴다(잠금 밖 경로). **사용자가 확인·수정한 뒤 직접 `spec/rules.json`으로 옮긴다**(이 이동이 승인). 이후 바꿀 때도 사람이 에디터로 고친다.
- `enforce: stop`은 결정적 검사가 가능할 때만 쓴다. 예: [L01] "이번 턴에 Edit·Write가 있었는데 마지막 답에 그 파일 경로가 없다". 모델 판단이 필요한 검사는 `none`으로 둔다(Anthropic: prompt·agent hook은 결정적이지 않다).
- 대장에 없는 규칙(새 `[Lnn]`)은 판정 파일 `## 대장에 없는 규칙`에 초안 항목으로 보여 준다. 사용자가 `rules.json`에 넣기 전까지 그 규칙은 판단 불가.

### 5.3 수집 (매일, 모델 없음)

- **언제:** SessionStart hook, 하루 한 번, 떼어 낸 프로세스(hook은 바로 끝남). dream과 같은 잠금·상태 방식.
- **무엇을:** 지난 수집 뒤 바뀐 jsonl에서 사용자 말마다 한 줄:
  `{ts, project, session, u(사용자 말 1500자), a_tail(직전 Claude 답 끝 300자), tools([{name, cmd(Bash·PowerShell 명령 앞 200자)}] — 직전 사용자 말 뒤부터 이 말 전까지의 도구 호출)}`
- 가린 뒤(`scrub`) `~/.rsi-kit/excerpts/YYYY-MM.jsonl`에 덧붙인다. 같은 발췌 id(원본 UTC 시각 + 사용자 말 해시)는 다시 쓰지 않는다 — 이어 하기·포크로 여러 세션 파일에 복사된 같은 말은 한 번만 저장한다(최종 검토 C1, 2026-11-23).
- 건너뛰기: 킷·dream·일지가 부른 `claude -p` 세션(애초에 기록이 안 남지만, 남은 루틴 세션은 dream의 `_is_routine_start` 같은 판정으로 뺀다), second-brain `journal_exclude` 폴더.
- 같은 수집에서 `registry.json`도 갱신한다(5.2).
- 매일인 이유: 30일 삭제 전에 빠짐없이 쌓기 위해. 비용 없음.

### 5.4 판정 (주 1회)

1. **대상과 기간:** `rules.json`에 있고 `removed`가 없는 규칙. 기간은 `text_since` 다음 날 ~ 지금. **문구가 바뀌면 그날부터 다시 센다**(바뀐 문구는 사실상 새 규칙이다).
2. **기회 세기:**
   - `tool` 규칙: 발췌의 `tools`를 정규식으로 결정적 판정(모델 없음). 예: [L06] = `gh pr merge`, [L20] = `git branch -D|git stash drop`.
   - `llm` 규칙: haiku에 발췌 묶음 + 해당 규칙 목록을 주고 발췌마다 `{rule, opportunity: yes|no|unknown}`.
3. **교정 세기:** haiku에 발췌 묶음 + 규칙 목록 + 각 `correction` 설명을 주고 `{excerpt, rule, correction: yes|no|unknown, quote(60자)}`. `unknown`은 세지 않는다. 프롬프트는 잠긴 `spec/judge-prompt.md`.
4. **판정(기본값은 `spec/thresholds.json`, 사람이 바꾼다):**

| 판정 | 조건 |
|---|---|
| 재발 | 교정 `yes`가 **서로 다른 세션 2개 이상** |
| 관찰 | 교정 `yes`가 세션 1개 |
| 지켜짐 | 교정 0, 기회가 서로 다른 세션 **5개 이상** |
| 판단 불가 | 교정 0, 기회 세션 5개 미만 |

   "지켜짐"은 "아직 어긴 적 없음" 정도의 뜻이다. 25회 무실패도 실패율 상한이 약 11%일 뿐이라 판정 파일에 기회 수를 함께 적는다.

5. **추천(재발한 규칙만, `rules.json` 값으로 기계적으로 정한다):**

| 조건 | 추천 |
|---|---|
| `enforced_by` 있음 | 제안 없음(표에만 "hook으로 강제 중") |
| `no_exceptions: true` 이고 `enforce`가 `none`이 아님 | **hook 후보**(종류 = `enforce`) |
| 그 밖(예외 있는 선호, 또는 강제 수단 없음) | **빼기 제안**. 근거에 "예외 있는 선호" 또는 "결정적 강제 수단 없음"을 적는다 |
| 판단 불가 | 추천 없음 |

   - `reminder`는 `type: step` 중 "알리기"류에만 쓴다. 자제형(`restraint`)에 상기는 효과가 거의 없다(Yang 외 arXiv 2607.26819: 기본 0%, 피드백을 줘도 최대 23%).
   - 빼지 않고 문구를 고쳐 남기고 싶으면 사용자가 `/inbox`에서 빼기 제안에 "문구 수정"으로 답한다. 킷은 새 문구를 만들지 않는다(1절 원칙).
   - 근거: Anthropic best-practices("hook은 예외가 0인 규칙용", "강조해도 계속 놓치면 hook 신호", "선호뿐인 hook은 지워라", "강제 세트는 작게"), Voria 외 arXiv 2611.13709(덜 지켜지는 유형은 맥락 파일 밖 강제).
6. **같은 제안을 다시 올리지 않는다([L07]):** 킷이 낸 번호 제안(빼기)과 hook 후보는 `state.json`에 (규칙 id, `text_since`)로 기록한다. 지난 판정 파일의 `## 처리 결과`(`/inbox`가 쓰는 `반영 · 거절 · 보류`)를 읽어 결정을 붙인다. **같은 문구에 대해서는 결정이 무엇이든 다시 제안하지 않는다.** 판정표에는 계속 "재발(제안함 YYYY-MM-DD, 거절)"처럼 보인다. 문구가 바뀌면(새 `text_since`) 새로 센다.
7. 판정 기록(`verdicts/`)과 인박스 파일을 쓴다(5.6). 판정 기록에 `spec/` 파일 해시를 남겨, 지난번과 바뀌었으면 판정 파일에 표시한다.

### 5.5 채점 보정

- 보정 확인은 **볼트가 아니라 `~/.rsi-kit/spec/review/YYYY-MM-DD.md`**에 둔다. 이유: 원문 발췌(500자 안팎)를 보여 줘야 확인할 수 있는데, 볼트는 동기화되고(반목표 4번), 잠금 밖이라 `/inbox`를 처리하는 에이전트가 체크할 수도 있다.
- 첫 판정과 `judge-prompt.md`가 바뀐 뒤 첫 판정에서 **최대 10건**을 고른다: 교정 `yes` 전부를 먼저, 남은 자리는 `no` 무작위. 항목마다 발췌, 판정, `- [ ] 맞음` / `- [ ] 틀림`.
- 사용자는 에디터로 직접 체크한다. 다음 판정이 읽어 `calibration.jsonl`에 쌓고 일치율을 표시한다. **답하지 않은 항목은 다음 판정에도 남는다**(새로 고르지 않고 같은 파일 유지).
- 볼트 판정 파일에는 "확인할 판정 N건 — `~/.rsi-kit/spec/review/<날짜>.md`를 에디터로 열어 체크"라는 한 줄만 넣고, 이 안내는 그 확인 파일이 처음 생긴 판정 때 한 번만 낸다(인박스 반복 방지). `rsi status`는 계속 보여 준다.
- 확인이 한 번도 안 끝났으면 모든 추천에 **"보정 전"** 표시를 붙인다. 일치율 합격선은 공식 기준이 없어 v1은 표시만 한다(Anthropic "Demystifying evals": 모델 채점은 사람 판단으로 보정하고 transcript를 직접 읽어 확인).

### 5.6 인박스 판정 파일

- **경로:** 볼트 `00_Inbox/YYYY-MM-DD dream 규칙 효과 판정.md` (같은 날 두 번이면 ` 2`). frontmatter `type: dream`, `tags: []`, `created`.
  - 확인한 것: `/inbox`는 frontmatter `type: dream`으로 4절 처리(`SKILL.md:17`), `brain-lint-reminder.py`도 frontmatter로 대기 알림(28~37줄), dream의 `proposed_before`는 날짜로 시작하고 이름에 `dream`이 든 30일 안의 `type: dream` 파일의 `- 규칙:` 줄을 읽어 같은 제안을 반복하지 않는다(136~151줄).
- **쓸 것이 있을 때만 쓴다:** 새 빼기 제안, 새 hook 후보, 대장에 없는 규칙, 처음 생긴 보정 확인 중 하나라도 있을 때. 없으면 `verdicts/`에만 남긴다.
- **내용 순서:**
  1. 머리 안내(🤖 RSI 킷 판정, 기간, 발췌 수, 보정 상태, spec 변경 여부)
  2. 번호 제안 — dream 형식 그대로 `## N. 빼기 · 전역|프로젝트 X · 대상 [Lnn]` + `- 규칙: (뺌)` / `- 이유:` / `- 근거:`(교정 세션 수, 60자 인용). **최대 5개.** `/inbox` 4절이 그대로 처리한다(빼기는 `rule` 문구가 없어도 된다).
  3. `## hook 후보` (번호 없음) — 규칙, hook 종류, 판정 근거. `/inbox`는 보여 주기만 한다. 만들기는 사용자가 승인한 별도 작업.
  4. `## 확인할 판정` (5.5의 한 줄 안내, 처음 한 번만)
  5. `## 대장에 없는 규칙` (5.2, 있을 때만)
  6. `## 전체 판정표` — 규칙 | 판정 | 교정 세션 수 | 기회 세션 수 | 문구 적용일 | 지난 제안·결정

### 5.7 잠금 (평가 분리)

- PreToolUse hook(`rsi-guard.py`): `Edit`·`Write`·`NotebookEdit`의 대상 경로가 `~/.rsi-kit/spec/` 아래이거나 설치된 판정 스크립트(`~/.claude/hooks/rsi-judge.py` 등)면 **deny**. `Bash`·`PowerShell` 명령 문자열에 그 경로가 들어 있어도 deny.
- **한계:** 셸 우회(경로를 쪼개 쓰기 등)는 완전히 막지 못한다. 그 부분은 지시 수준이다. 사람은 에디터로 직접 고친다.
- 킷 자신의 파이썬 프로세스는 도구 호출이 아니라서 잠금에 걸리지 않는다(`review/` 파일 생성 등).
- `rsi init`의 초안(`spec/rules.draft.json`)은 잠금 예외다. 사용자가 `rules.json`으로 옮기면 잠긴다.

### 5.8 설치와 실행

- `install.ps1` / `install.sh`: hook 복사, `~/.claude/settings.json`에 SessionStart(수집·주간 판정)와 PreToolUse(잠금) 등록. **`settings.json`을 바꾸므로 사용자가 직접 실행하거나 승인한다.** 제거 옵션도 둔다.
- 수동 명령: `py -3 ~/.claude/hooks/rsi.py init | collect | judge | status`.
- `status`: 마지막 수집·판정 시각, 발췌 수, 규칙별 최근 판정, 보정 일치율과 남은 확인 건수, CLI 로그인 상태.

### 5.9 비용과 개인정보

- 수집은 모델을 부르지 않는다. 판정은 주 1회 haiku. 발췌 묶음 크기와 1회 상한은 dream(120KB 묶음, 1회 3묶음)을 참고해 정하고, 넘친 것은 다음 주로 넘긴다.
- **haiku로 보내는 것:** dream과 같은 사용자 말 1500자 + 답 끝 300자에, **도구 이름 목록만** 더한다(예: "Edit, Bash"). **도구 명령 문자열·파일 경로는 보내지 않는다.** 명령 문자열은 `tool` 규칙의 결정적 판정에만 로컬로 쓴다. 이 점이 dream보다 늘어나는 유일한 노출이다.
- 대화 발췌는 `~/.rsi-kit/`에만 둔다. 볼트 판정 파일에는 60자 이하 인용만 넣는다.

## 6. 정한 것

**사용자가 정함 (2026-11-23):** 목적(안 먹히는 규칙은 빼거나 hook으로 강제), 방식 B, 승인은 `/inbox`.

**설계자 판단(고칠 수 있음):**
- 판정 4단계와 기준값(서로 다른 세션 2개, 기회 세션 5개).
- 매일 수집 + 주 1회 판정, 상태 폴더 `~/.rsi-kit/`.
- 규칙 정보를 사실(`registry.json`, 킷 관리)과 판정 기준(`spec/rules.json`, 사람 소유)으로 나누고, 문구가 바뀌면 그날부터 다시 세기. (검토자 질문 1)
- 같은 문구에 대한 제안은 결정과 상관없이 다시 올리지 않기 — [L07]을 따름. (검토자 질문 1)
- 보정 확인은 볼트 밖 잠긴 `spec/review/`에서, 답 안 한 항목은 다음에도 남기기. (검토자 질문 2)
- 추천은 빼기와 hook 후보 둘만, 새 문구는 만들지 않기 — 사용자 목적과 평가 분리 원칙을 따름. (검토자 질문 3)
- 강제 수단이 없는 자제형 규칙은 hook 후보가 아니라 빼기 제안, 모델 판단이 필요한 검사는 `enforce: none`. (검토자 질문 4)
- haiku에는 도구 이름만 보내고 명령 문자열은 보내지 않기. (검토자가 찾은 틀린 가정)
- dream 형식 재사용, hook 후보는 번호 없는 절, 보정 10건, 규칙 유형 3가지, 인박스에 쓸 것이 없으면 쓰지 않기.

## 7. 빌더에게 맡김

- jsonl 파싱 세부(도구 결과 제외, 사이드체인·서브에이전트 기록 처리), `scrub` 패턴, 묶음 크기·1회 상한, 테스트 구성.
- `judge-prompt.md` 첫 문구(사용자 확인 뒤 잠금), `rules.draft.json` 초안 생성 휴리스틱, 제안 문구와 CLAUDE.md 줄을 맞추는 방법.

## 8. 근거

- 볼트: `40_Resources/재귀적 자기 개선(RSI) 엔지니어링.md`(2026-11-20 조사)
- second-brain-kit 코드(2026-11-23 정적 확인): `hooks/brain-dream.py`(136~151, 186, 256~257, 273~288줄), `hooks/brain_common.py`(6, 296~316, 643~653줄), `hooks/brain-lint-reminder.py`(28~37줄), `skills/inbox/SKILL.md`(17, 35~49줄)
- 2026-11-23 조사(원문 검증):
  - Claude Code best practices — https://code.claude.com/docs/en/best-practices
  - Claude Code memory — https://code.claude.com/docs/en/memory
  - Claude Code hooks — https://code.claude.com/docs/en/hooks
  - Anthropic, Demystifying evals for AI agents (2026-01-09) — https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
  - Voria 외, arXiv 2611.13709 (2026-11-10): 지침별 적용 커밋만 세기, 2,053개 중 54.4% 항상·37.3% 전혀 안 지킴
  - Yang 외, arXiv 2607.26819: 자제형(거부·넘기기) 규칙은 기본 0%, 피드백 최대 23% (초록의 "어떤 조건에서도 0%"는 표와 달라 정정)
  - ContextCov v2, arXiv 2603.00822: 실행 검사 준수 88.3% vs 프롬프트만 67.0% (SWE-bench Lite 300건)
  - developertoolkit, Pruning context files (2026-08-04, 갱신 2026-11-21): 뺀 규칙은 서로 다른 세션에서 같은 실패가 2번 된 뒤에만 복원(개인 절차, 효과 검증 없음)
  - ACE, arXiv 2510.04618: bullet별 helpful/harmful 카운터, 라벨 없는 온라인 조건에서 FiNER -3.4
