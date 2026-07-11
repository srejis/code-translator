# previbemap code segmenter

LLM 번역 전에 소스 코드를 **입력 1개 → 번역문 1개** 단위로 자르는 Python 도구입니다.
초기 기준 저장소는 `srejis/previbemap`입니다.

## 현재 분리 원칙

- 줄바꿈과 들여쓰기가 아니라 Tree-sitter AST로 문장 경계를 판별합니다.
- 함수·클래스·조건문·반복문 등의 정의/헤더는 부모 단위 1개로 만듭니다.
- 부모 단위의 `code`에는 헤더만 두고, 본문의 각 문장을 자식 단위로 재귀 추출합니다.
- 여러 줄의 매개변수·호출 입력값·조건식·연산식은 상위 문장에 포함합니다.
- 여러 줄 데이터가 변수 대입의 직접 값이면 전체 대입문을 한 단위로 유지합니다.
- 호출 입력값으로 전달된 객체/배열은 여러 줄이어도 호출문 한 단위에 유지합니다.
- import, export, 정의, 타입, 주석도 단위에 포함합니다.
- 빈 줄과 특수기호만 있는 항목은 제외합니다.
- 한 파일 안에서 최대 10개씩 LLM 입력 배치를 함께 생성합니다.

## 설치

```bash
python -m venv .venv
# Windows
.venv\\Scripts\\activate
# macOS / Linux
# source .venv/bin/activate

pip install -r requirements.txt
```

## 기본 실행

Windows에서 다음 파일을 실행합니다.

```bat
run_code_translator.bat
```

실행 흐름은 다음과 같습니다.

```text
폴더 선택 → 기존 기록 로드 또는 새 분석 → 브라우저 시각화
```

선택한 프로젝트의 기록은 소스 프로젝트 내부가 아니라 이 도구의
`outputs/projects/<폴더명>-<경로 해시>/`에 저장됩니다. 같은 프로젝트를 다시
선택하면 기존 기록을 즉시 사용합니다. 최신 소스로 다시 만들려면 런처의
`기록 삭제 후 새로 분석` 버튼을 누릅니다. 새 분석이 실패해도 기존 정상 기록은
보존됩니다.

분석 진행 중에는 런처에 현재 파일과 진행 개수가 표시됩니다. 준비가 끝나면
사용 가능한 로컬 포트에서 뷰어가 실행되고 기본 브라우저가 자동으로 열립니다.

## 주요 출력 필드

```json
{
  "id": "unit_000001_ab12cd34",
  "parent_id": null,
  "file": "previbemap-landing/components/use/UseApp.tsx",
  "language": "tsx",
  "node_type": "function_declaration",
  "start_line": 181,
  "end_line": 186,
  "code": "function statusClassName(status: ReviewStatus | undefined) {}",
  "raw_code": "function statusClassName(...) { ... }"
}
```

- `code`: LLM에 보낼 본문 제거 버전
- `raw_code`: 원본 범위
- `parent_id`: 함수·클래스·데이터 구조 안의 자식 관계
- `warnings`: 너무 큰 단위 등 수동 확인이 필요한 항목
- `llm_batches`: 같은 파일 단위로 최대 10개씩 묶은 후속 입력

## 의도적으로 아직 고정하지 않은 부분

- TSX의 거대한 JSX 반환문을 JSX 태그별로 분리할지 여부
- `switch case` 내부를 별도 가상 본문으로 더 세밀하게 자르는 규칙
- 한 줄짜리 대형 데이터와 여러 줄짜리 소형 데이터의 분리 기준

현재 버전은 잘못 나누는 것보다 `large_unit` 경고로 남기는 쪽을 우선합니다.
실제 `previbemap` 출력 JSON을 검토한 뒤 예외 규칙을 추가하는 방식이 적합합니다.

## 코드 번역 단위 규칙

- 독립된 코드 문법 단위 하나를 자연어 문장 하나와 대응한다.
- 코드의 목적, 기능, 역할을 추론하거나 요약하지 않는다.
- 데코레이터는 각각 독립된 번역 단위로 만든다.
- 함수와 클래스 정의는 데코레이터와 분리한다.
- 함수·클래스 정의 단위의 `code`에는 정의 헤더만 넣는다.
- 함수와 클래스 본문의 독립 문장은 정의 단위의 자식으로 저장한다.
- `if`, `elif`, `else`, `for`, `while`, `try`, `except`, `finally`는 각각 독립된 제어문 헤더 단위로 만든다.
- 제어문 조건식은 해당 제어문 단위에 포함하고 별도로 분리하지 않는다.
- 조건이 길어도 글자 수나 의미 그룹을 기준으로 나누지 않는다.
- 변수에 대입되는 리스트·딕셔너리·튜플·세트는 여러 줄이어도 전체 대입문 하나로 유지한다.
- 객체와 배열 리터럴 내부 항목은 독립 번역 단위로 자동 분해하지 않는다.
- `parent_id`와 `depth`는 원본 중첩 위치를 보존하기 위한 메타데이터다.
- 부모와 자식 번역문을 결합해 상위 기능을 만들지 않는다.
- `code`에는 실제 번역할 코드만 저장하고 합성 자리표시자를 넣지 않는다.
- `raw_code`에는 원본 AST 범위 전체를 보존한다.
- `display_start_line`과 `display_end_line`은 시각화 범위다.
- `start_line`과 `end_line`은 원본 AST 전체 범위다.

## 고급 사용법

자동 테스트나 디버깅에서는 프로젝트를 직접 지정할 수 있습니다.

```bat
D:\anaconda3\envs\code_trans\python.exe code_translator_app.py --project "D:\path\to\project"
D:\anaconda3\envs\code_trans\python.exe code_translator_app.py --project "D:\path\to\project" --force
D:\anaconda3\envs\code_trans\python.exe code_translator_app.py --project "D:\path\to\project" --no-browser
```

`--force`는 기존 기록을 안전하게 교체하고, `--no-browser`는 서버만 실행합니다.
서버 종료는 실행 터미널에서 `Ctrl+C`입니다.

### 분석기와 뷰어 개별 실행

`segment_code.py`가 생성한 대형 JSON을 IDE 형태로 검수하려면 로컬 뷰어를 실행합니다.

### 분리 결과 생성

```bat
python segment_code.py D:\project\12_Gen_Code\codegraph-mvp ^
  --output outputs\backend_unit.json ^
  --batch-size 10
```

### 뷰어 실행

```bat
D:\anaconda3\envs\code_trans\python.exe code_unit_viewer.py outputs\backend_unit.json
```

JSON의 `root` 값이 현재 원본 저장소 위치와 다를 때만 `--root`를 사용합니다.

```bat
D:\anaconda3\envs\code_trans\python.exe code_unit_viewer.py outputs\backend_unit.json ^
  --root D:\project\12_Gen_Code\codegraph-mvp
```

실행 후 브라우저에서 다음 기능을 사용할 수 있습니다.

- 폴더별 파일 트리 펼치기
- 파일명과 경로 검색
- 경고 파일 및 파싱 오류 파일 필터링
- 원본 코드 위의 번역 단위 시작선과 종료선 확인
- 단위 클릭 후 `code`, `raw_code`, 부모·자식 관계 확인
- 단위 목록 검색
- 대형 JSON의 SQLite 색인 재사용

첫 실행에서는 JSON 옆에 다음 캐시가 생성됩니다.

```text
backend_unit.json.viewer.sqlite3
```

JSON을 다시 생성한 경우 캐시는 자동으로 갱신됩니다. 강제로 다시 만들려면:

```bat
D:\anaconda3\envs\code_trans\python.exe code_unit_viewer.py outputs\backend_unit.json --rebuild-index
```

서버 종료는 실행 터미널에서 `Ctrl+C`입니다.
