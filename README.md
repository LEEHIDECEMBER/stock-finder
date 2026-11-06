# 선호 주식 찾기

국내(코스피·코스닥)와 미국(S&P500) 주식을 PER·PBR·ROE·배당수익률·기간 상승률(1일~1년)·시가총액·거래대금·거래량,
추세·수급 지표(52주 고가/저가 대비, 거래량 급증, RSI, 20일선 이격도, 외국인·기관 순매수)와
신호(정배열, 골든크로스, 52주 신고가, 52주 신저가), 관심 분야(반도체, AI, 자동차 등 15개)로 걸러 보는 웹 앱(PWA).

- 앱: `index.html` 한 파일 (순수 HTML/CSS/JS), GitHub Pages로 배포
- 데이터: GitHub Actions가 매일 아침(한국 시간 화~토 06:17) `scripts/fetch_data.py`를 실행해서 `data.json`을 새로 만든다
- 저장: 저장한 조건·관심종목은 폰에 저장되고, Firebase를 연결하면 계정에도 저장된다

## 파일 구성

```
index.html                    앱 (샘플 데이터 내장: data.json을 못 읽을 때 사용)
data.json                     종목 데이터 (처음엔 가상 샘플 → Actions가 실제 데이터로 교체)
sw.js, manifest.webmanifest   PWA (홈 화면 설치, 오프라인)
icon-192.png, icon-512.png, apple-touch-icon.png
scripts/fetch_data.py         데이터 수집 (pykrx, FinanceDataReader, yfinance)
requirements.txt              파이썬 라이브러리 목록
.github/workflows/update-data.yml   매일 자동 실행
```

## 배포 순서

1. GitHub에서 새 저장소 만들기 (Public, 이름은 소문자 추천. 예: `stock-finder`)
2. 이 폴더의 파일을 **폴더 구조 그대로** 올리기
   - 웹 업로드는 `.github` 같은 점(.)으로 시작하는 폴더가 안 보일 수 있다.
     그럴 땐 저장소에서 **Add file → Create new file**을 누르고 이름 칸에
     `.github/workflows/update-data.yml`을 입력한 뒤 내용을 붙여 넣는다.
3. **Settings → Pages**: Source = Deploy from a branch, Branch = `main` / `(root)` → Save
4. **Settings → Actions → General → Workflow permissions**: *Read and write permissions* 선택 → Save
   (Actions가 data.json을 커밋하려면 필요)
5. **Actions 탭 → 주식 데이터 업데이트 → Run workflow**로 첫 실행 (10~25분)
   - 끝나면 data.json이 실제 데이터로 바뀌고, 앱 위쪽의 "샘플 데이터" 안내가 사라진다
6. 아이폰 Safari로 `https://<아이디>.github.io/<저장소>/` 접속 → 공유 → 홈 화면에 추가

## Firebase 연결 (선택: 조건·관심종목을 계정에 저장)

1. Firebase 콘솔에서 새 프로젝트 만들기 (Spark 무료 플랜 그대로)
2. Authentication → 이메일/비밀번호 사용 설정 → 사용자 탭에서 내 계정 직접 추가
3. Authentication → 설정 → 승인된 도메인에 `<아이디>.github.io` 추가
4. Firestore Database 만들기 (위치 asia-northeast3 서울) → 규칙에 아래 붙여 넣기

   ```
   rules_version = '2';
   service cloud.firestore {
     match /databases/{database}/documents {
       match /users/{uid} {
         allow read, write: if request.auth != null && request.auth.uid == uid;
       }
     }
   }
   ```

5. 프로젝트 설정 → 내 앱 → 웹 앱 추가 → 나오는 `firebaseConfig`를
   `index.html`의 `const FIREBASE_CONFIG = null;` 자리에 넣기

   ```js
   const FIREBASE_CONFIG = { apiKey: "...", authDomain: "...", projectId: "...", appId: "..." };
   ```

6. 앱 설정 탭에서 로그인 → 이 기기의 조건·관심종목이 계정에 올라가고, 이후 자동 동기화

데이터 위치: `users/{내 uid}` 문서 하나 = `{ presets: [...], watch: [...], updatedAt }`

## 관심 분야(테마)

`scripts/fetch_data.py`의 `THEMES` 목록에서 정한다. 종목마다 업종·주요제품 설명에 들어 있는 단어로 분야를 붙이고,
AI처럼 업종명으로 알기 어려운 분야는 종목코드를 직접 적어 둔다.

- 분야 추가: `THEMES` **맨 뒤에** 한 줄 추가 (순서가 곧 저장 번호라서 중간에 끼우거나 순서를 바꾸면 안 된다)
- 잘못 붙거나 빠진 종목이 있으면 단어나 종목코드를 고친다
- 앱은 data.json의 `meta.themes`에서 목록을 읽기 때문에 앱 코드는 고칠 필요가 없다

## 데이터 메모

| 항목 | 국내 | 미국 |
|---|---|---|
| 출처 | pykrx (KRX) | yfinance (Yahoo Finance) |
| PER·PBR·배당 | KRX 제공값 | trailingPE, priceToBook, dividendRate÷주가 |
| ROE | EPS ÷ BPS로 계산 (근사값) | returnOnEquity |
| 시가총액·거래대금 | 원 | 달러 × 환율로 원화 환산 |
| 상승률 | 하루 등락률 + 1주/1개월/3개월/6개월/1년 전 종가 대비 | 같음 |
| 추세 지표·신호 | 약 1년치 일별 종가·거래량으로 계산 | 같음 |
| 외국인·기관 순매수 | 최근 5거래일 순매수 금액 | 없음 (국내만) |

추세 지표 계산 방법 (`scripts/fetch_data.py`의 `tech()`)
- 52주 최고가/최저가 대비: 최근 1년 종가 중 최고·최저와 비교
- 거래량 급증: 오늘 거래량 ÷ 직전 20거래일 평균
- RSI(14): 와일더 방식 14일
- 20일선 이격도: 현재가 ÷ 20일 이동평균 - 1
- 정배열: 5일 > 20일 > 60일 > 120일 이동평균
- 골든크로스: 최근 5거래일 안에 20일선이 60일선 위로 올라감
- 52주 신고가: 오늘 종가가 직전 1년 종가 최고 이상
- 52주 신저가: 오늘 종가가 직전 1년 종가 최저 이하

- 실시간이 아니라 **전 영업일 종가 기준**
- 국내 추세 지표는 수정주가가 아닌 일별 종가로 계산해서, 1년 안에 액면분할·병합이 있었던 종목은 52주 고가/저가 값이 어긋날 수 있다
- 적자 기업의 PER은 비워 두므로 PER 조건을 켜면 자동으로 빠진다
- pykrx가 막히면 FinanceDataReader로 시세만 받고(이때 PER 등은 비어 있음), 한쪽 시장 수집이 통째로 실패하면 예전 데이터를 유지한다
- 무료 공개 데이터라 늦거나 틀릴 수 있다. 개인 공부·참고용
