"""
선호 주식 찾기 - 데이터 수집 스크립트
GitHub Actions가 하루 한 번 실행해서 저장소 맨 위의 data.json을 새로 만든다.

- 국내(코스피·코스닥)
  · KRX 로그인 정보(GitHub Secrets의 KRX_ID, KRX_PW)가 있으면 pykrx로 전부
    (시세·시총·PER/PBR/EPS/BPS/배당, 기간 등락률, 1년치 일별 시세, 외국인·기관 순매수)
  · 없거나 실패하면 로그인 없이: FinanceDataReader 종목 목록 + 야후 1년치 일별 시세
    + 네이버 시가총액 페이지의 PER·ROE (PBR = PER × ROE ÷ 100). 배당·수급은 비어 있음
- 미국(S&P500): FinanceDataReader/위키백과로 종목 목록, yfinance로 시세·재무지표
- 한쪽 수집이 실패하면 예전 data.json의 그 시장 데이터를 그대로 둔다 (앱이 비지 않게)

data.json 형식 (용량을 줄이려고 열 이름 + 행 배열):
  t 코드, n 이름, m 시장(KOSPI/KOSDAQ/SP500), sec 업종, p 현재가(현지 통화),
  mc 시가총액(원), per, pbr, roe(%), div 배당수익률(%), vol 거래량(주), val 거래대금(원),
  r1d r1w r1m r3m r6m r1y 기간 상승률(%),
  h52 52주 최고가 대비(%), l52 52주 최저가 대비(%), vr 거래량 배수(오늘 ÷ 20일 평균),
  rsi RSI(14), d20 20일 이동평균 대비(%), fb 외국인 5일 순매수(원), ib 기관 5일 순매수(원),
  fl 신호 비트(1 정배열, 2 골든크로스 5일 내, 4 52주 신고가, 8 52주 신저가),
  th 분야 비트 (THEMES 순서대로 1, 2, 4, ...)
"""
import io
import json
import math
import os
import re
import sys
import time
import traceback
import warnings
from datetime import datetime, timedelta, timezone

import pandas as pd

warnings.filterwarnings("ignore")

KST = timezone(timedelta(hours=9))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data.json")
FIELDS = ["t", "n", "m", "sec", "p", "mc", "per", "pbr", "roe", "div", "vol", "val",
          "r1d", "r1w", "r1m", "r3m", "r6m", "r1y",
          "h52", "l52", "vr", "rsi", "d20", "fb", "ib", "fl", "th"]
WINDOWS = {"r1w": 7, "r1m": 30, "r3m": 91, "r6m": 182, "r1y": 365}
TECH_KEYS = ["h52", "l52", "vr", "rsi", "d20"]

# ---------------------------------------------------------------- 분야(테마)
# (id, 이름, 업종·주요제품 글자에서 찾을 단어(정규식), 직접 지정 종목)
# 국내는 KRX 업종명 + 주요제품, 미국은 GICS 섹터 + 야후 섹터·산업으로 판단한다.
# ⚠️ 순서가 곧 비트 번호라서, 새 분야는 맨 뒤에만 추가할 것
THEMES = [
    ("semi", "반도체", ["반도체", "Semiconductor"], ["005930"]),
    ("ai", "AI", ["인공지능", r"(?<![A-Za-z])AI(?![A-Za-z])", "HBM", "머신러닝", "딥러닝"],
     ["005930", "000660", "035420", "NVDA", "AMD", "AVGO", "MSFT", "GOOGL", "GOOG", "META", "AMZN",
      "ORCL", "PLTR", "SMCI", "ANET", "MU", "CRM", "NOW", "DELL", "VRT"]),
    ("auto", "자동차", [r"자동차(용 엔진| 제조| 신품| 차체| 재제조| ?부품)", "완성차", "Auto Manufacturers", "Auto Parts", "Automobile"], []),
    ("battery", "2차전지", ["이차전지", "2차전지", "축전지", "배터리", "양극재", "음극재", "전해액", "분리막", "리튬", "Lithium"], ["ALB"]),
    ("bio", "바이오·헬스케어", ["의약", "바이오", "제약", "의료", "진단", r"Health ?care", "Biotech", "Drug", "Medical", "Diagnostics"], []),
    ("soft", "소프트웨어·인터넷", ["소프트웨어", "포털", "인터넷", "플랫폼", "클라우드", "Software", "Internet", "Information Technology Services"], []),
    ("game", "게임·엔터", ["게임", "엔터테인먼트", "음반", "오디오물", "영화", "방송 프로그램", "Entertainment", "Electronic Gaming", "Broadcasting"], []),
    ("fin", "금융", ["은행", "증권", "보험", "신용카드", "여신", "저축기관", "금융 지원", "금융지주", "신한지주", "KB금융", "Financial", "Bank", "Insurance", "Capital Markets"], []),
    ("defense", "방산·우주", ["무기", "방위", "항공기", "우주", "총포", "탄약", "Aerospace", "Defense"], []),
    ("ship", "조선·해운", ["선박", "조선", "해운", "해상 운송", "Marine Shipping"], []),
    ("energy", "에너지·전력", ["발전", "전력", "원자력", "원전", "석유", "정유", "가스", "태양광", "풍력", "변압기", "절연선",
                          "Energy", "Utilities", "Oil", "Gas", "Solar"], []),
    ("consumer", "화장품·식품", ["화장품", "식료품", "식품", "음료", "주류", "담배", "Consumer Defensive", "Consumer Staples",
                             "Beverages", "Packaged Foods", "Household", "Personal Products"], []),
    ("material", "화학·철강", ["화학", "철강", "금속", "비철", "Chemicals", "Steel", "Basic Materials", "Metals"], []),
    ("build", "건설·인프라", ["건설", "토목", "건축", "시멘트", "Construction", "Building", "Infrastructure"], []),
    ("robot", "로봇·자동화", ["로봇", "자동화", "Robot", "Automation"], ["ISRG", "ROK", "TER"]),
]
_THEME_RE = [re.compile("|".join(words), re.I) for _, _, words, _ in THEMES]


def theme_bits(code, text):
    """종목코드 + 업종·제품 설명 글자로 분야 비트 계산"""
    bits = 0
    for i, (_, _, _, codes) in enumerate(THEMES):
        if code in codes or (text and _THEME_RE[i].search(text)):
            bits |= 1 << i
    return bits



def num(x, nd=2):
    """숫자로 바꾸고, 빈 값·무한대는 None"""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, nd)


def pos(x, nd=2):
    """0 이하(적자 PER 등)는 의미 없는 값이라 None"""
    v = num(x, nd)
    return v if v is not None and v > 0 else None


def log(*a):
    print(datetime.now(KST).strftime("%H:%M:%S"), *a, flush=True)


# ---------------------------------------------------------------- 공통: 추세 지표
def tech(close, volume=None):
    """일별 종가(·거래량) 시리즈로 추세 지표 계산. 데이터가 모자라면 None"""
    out = {k: None for k in TECH_KEYS}
    out["fl"] = 0
    s = close.dropna()
    s = s[s > 0]
    if len(s) < 30:
        return out
    p = float(s.iloc[-1])
    year = s[s.index > s.index[-1] - pd.Timedelta(days=365)]
    out["h52"] = round((p / year.max() - 1) * 100, 1)
    out["l52"] = round((p / year.min() - 1) * 100, 1)

    if volume is not None:
        v = volume.reindex(s.index).dropna()
        if len(v) >= 21:
            avg = float(v.iloc[-21:-1].mean())
            if avg > 0:
                out["vr"] = round(float(v.iloc[-1]) / avg, 2)

    d = s.diff().dropna()  # RSI(14), 와일더 방식
    au = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean().iloc[-1]
    ad = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean().iloc[-1]
    out["rsi"] = round(100.0 if ad == 0 else 100 - 100 / (1 + au / ad), 1)

    ma = {n: s.rolling(n).mean() for n in (5, 20, 60, 120)}
    out["d20"] = round((p / ma[20].iloc[-1] - 1) * 100, 1)

    fl = 0
    if len(s) >= 120 and ma[5].iloc[-1] > ma[20].iloc[-1] > ma[60].iloc[-1] > ma[120].iloc[-1]:
        fl |= 1  # 정배열
    gap = (ma[20] - ma[60]).dropna()
    if len(gap) >= 6 and gap.iloc[-1] > 0 and (gap.iloc[-6:-1] <= 0).any():
        fl |= 2  # 최근 5거래일 안에 골든크로스
    prev = year.iloc[:-1]
    if len(prev) >= 200 and p >= prev.max():
        fl |= 4  # 52주 신고가
    if len(prev) >= 200 and p <= prev.min():
        fl |= 8  # 52주 신저가
    out["fl"] = fl
    return out


def hist_returns(s):
    """일별 종가 시리즈로 [1일, 1주, 1개월, 3개월, 6개월, 1년] 상승률"""
    s = s.dropna()
    s = s[s > 0]
    if len(s) < 2:
        return [None] * 6
    last_d, last = s.index[-1], float(s.iloc[-1])
    out = [round((last / float(s.iloc[-2]) - 1) * 100, 1)]
    for days in WINDOWS.values():
        past = s[s.index <= last_d - pd.Timedelta(days=days)]
        out.append(round((last / float(past.iloc[-1]) - 1) * 100, 1) if len(past) else None)
    return out


def kr_desc():
    """KRX-DESC: 업종(Industry)·주요제품(Products). 표시용 업종, 분야 판단용 글자"""
    sectors, descs = {}, {}
    try:
        import FinanceDataReader as fdr
        d = fdr.StockListing("KRX-DESC")
        col = lambda c: d[c].fillna("").astype(str) if c in d else pd.Series("", index=d.index)
        codes = d["Code"].astype(str)
        sectors = dict(zip(codes, col("Industry")))
        descs = dict(zip(codes, col("Industry") + " " + col("Products") + " " + col("Sector")))
    except Exception as e:
        log("KR 업종 정보 실패(무시)", e)
    return sectors, descs


# ---------------------------------------------------------------- 국내 (KRX 로그인)
def kr_history(stock, base):
    """최근 약 1년치 전 종목 일별 종가·거래량 (날짜 하나에 한 번 호출, 약 250회)"""
    base_dt = datetime.strptime(base, "%Y%m%d")
    frm = (base_dt - timedelta(days=380)).strftime("%Y%m%d")
    try:
        dates = [pd.Timestamp(d).strftime("%Y%m%d") for d in stock.get_previous_business_days(fromdate=frm, todate=base)]
    except Exception:
        dates = [d.strftime("%Y%m%d") for d in pd.bdate_range(frm, base)]
    closes, vols, last_chg = {}, {}, {}
    for i, d in enumerate(dates):
        for attempt in range(3):
            try:
                df = stock.get_market_ohlcv(d, market="ALL")
                break
            except Exception as e:
                df = None
                time.sleep(2 + attempt * 3)
        if df is None or df.empty or df["종가"].sum() == 0:
            continue  # 휴장일
        closes[d], vols[d] = df["종가"], df["거래량"]
        if d == base and "등락률" in df:
            last_chg = df["등락률"].to_dict()
        if i % 50 == 0:
            log("KR 일별 시세", i, "/", len(dates), d)
        time.sleep(0.2)
    C = pd.DataFrame(closes).T
    V = pd.DataFrame(vols).T
    C.index = pd.to_datetime(C.index)
    V.index = pd.to_datetime(V.index)
    return C.sort_index(), V.sort_index(), last_chg


def kr_flows(stock, frm, base):
    """최근 5거래일 외국인·기관 순매수 금액(원)"""
    fn = getattr(stock, "get_market_net_purchases_of_equities", None) or \
        getattr(stock, "get_market_net_purchases_of_equities_by_ticker")
    res = {}
    for inv, key in (("외국인", "fb"), ("기관합계", "ib")):
        d = {}
        for mk in ("KOSPI", "KOSDAQ"):
            try:
                df = fn(frm, base, mk, inv)
                d.update(df["순매수거래대금"].to_dict())
            except Exception as e:
                log("KR 수급 실패", inv, mk, e)
            time.sleep(1)
        res[key] = d
    return res


def fetch_kr():
    from pykrx import stock

    base = stock.get_nearest_business_day_in_a_week()  # 'YYYYMMDD', 가장 최근 영업일
    log("KR 기준일", base)
    base_dt = datetime.strptime(base, "%Y%m%d")

    cap = stock.get_market_cap(base, market="ALL")           # 종가 시가총액 거래량 거래대금 상장주식수
    fund = stock.get_market_fundamental(base, market="ALL")  # BPS PER PBR EPS DIV DPS
    if cap.empty:
        raise RuntimeError("pykrx 시가총액 데이터가 비어 있음")

    market_of = {}
    for mk in ("KOSPI", "KOSDAQ"):
        for t in stock.get_market_ticker_list(base, market=mk):
            market_of[t] = mk

    names, rets = {}, {}
    for key, days in WINDOWS.items():
        frm = stock.get_nearest_business_day_in_a_week((base_dt - timedelta(days=days)).strftime("%Y%m%d"))
        try:
            ch = stock.get_market_price_change(frm, base, market="ALL")  # 종목명 시가 종가 변동폭 등락률 ...
            rets[key] = ch["등락률"].to_dict()
            names.update(ch["종목명"].to_dict())
            log("KR", key, frm, "→", base, len(ch))
        except Exception as e:
            log("KR 상승률 실패", key, e)
            rets[key] = {}
        time.sleep(1)

    # 일별 시세 → 추세 지표, 하루 등락률
    C = V = None
    last_chg, techs = {}, {}
    try:
        C, V, last_chg = kr_history(stock, base)
        log("KR 일별 시세", C.shape)
        for t in C.columns:
            techs[t] = tech(C[t], V[t] if t in V else None)
    except Exception as e:
        log("KR 추세 지표 실패(무시)", e)

    # 수급: 최근 5거래일
    flows = {"fb": {}, "ib": {}}
    try:
        frm5 = C.index[-5].strftime("%Y%m%d") if C is not None and len(C) >= 5 else \
            (base_dt - timedelta(days=7)).strftime("%Y%m%d")
        flows = kr_flows(stock, frm5, base)
    except Exception as e:
        log("KR 수급 실패(무시)", e)

    sectors, descs = kr_desc()

    rows = []
    for t, r in cap.iterrows():
        mk = market_of.get(t)
        if not mk:
            continue  # 코넥스 등 제외
        p = num(r.get("종가"), 0)
        if not p:
            continue  # 거래정지 등
        n = names.get(t) or stock.get_market_ticker_name(t)
        f = fund.loc[t] if t in fund.index else {}
        g = (lambda k: f.get(k)) if len(f) else (lambda k: None)
        eps, bps = num(g("EPS")), num(g("BPS"))
        roe = round(eps / bps * 100, 1) if eps is not None and bps and bps > 0 else None  # ROE ≈ EPS ÷ BPS
        tk = techs.get(t) or {k: None for k in TECH_KEYS + ["fl"]}
        rows.append([
            t, n, mk, (sectors.get(t) or "")[:20], p,
            num(r.get("시가총액"), 0),
            pos(g("PER")), pos(g("PBR")), roe, num(g("DIV")),
            num(r.get("거래량"), 0), num(r.get("거래대금"), 0),
            num(last_chg.get(t), 1),
            *[num(rets[k].get(t), 1) for k in WINDOWS],
            *[tk[k] for k in TECH_KEYS],
            num(flows["fb"].get(t), 0), num(flows["ib"].get(t), 0),
            tk["fl"] or 0,
            theme_bits(t, f"{descs.get(t, '')} {n}"),
        ])
    return rows, base_dt.strftime("%Y-%m-%d")


def naver_per_roe():
    """네이버 금융 시가총액 페이지(코스피·코스닥 전체, 페이지당 50종목)에서 PER·ROE"""
    import requests
    out = {}
    ua = {"User-Agent": "Mozilla/5.0 (stock-finder; personal study project)"}
    for sosok in (0, 1):  # 0 코스피, 1 코스닥
        for page in range(1, 80):
            try:
                r = requests.get("https://finance.naver.com/sise/sise_market_sum.naver",
                                 params={"sosok": sosok, "page": page}, headers=ua, timeout=20)
                r.encoding = "euc-kr"
                html = r.text
            except Exception as e:
                log("네이버 실패", sosok, page, e)
                break
            codes = re.findall(r'code=(\d{6})"[^>]*class="tltle"', html)
            if not codes:
                if page == 1:
                    log("네이버 응답 확인", r.status_code, len(html), "tltle" in html, html[:200].replace("\n", " "))
                break
            try:
                tb = [t for t in pd.read_html(io.StringIO(html)) if "종목명" in t.columns][0]
                tb = tb.dropna(subset=["종목명"])
                tb = tb[tb["종목명"].astype(str).str.strip() != ""].reset_index(drop=True)
                for code, (_, row) in zip(codes, tb.iterrows()):
                    out[code] = (pos(row.get("PER")), num(row.get("ROE"), 1))
            except Exception as e:
                log("네이버 표 해석 실패", sosok, page, e)
            time.sleep(0.3)
    log("네이버 PER·ROE", len(out))
    return out


def fetch_kr_nologin():
    """KRX 로그인 없이: FDR 종목 목록 + 야후 1년치 시세 + 네이버 PER·ROE"""
    import FinanceDataReader as fdr
    import yfinance as yf

    lst = fdr.StockListing("KRX")
    lst["Market"] = lst["Market"].astype(str).str.upper()
    lst = lst[lst["Market"].isin(["KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"])].copy()
    lst["mk"] = lst["Market"].map(lambda m: "KOSPI" if m == "KOSPI" else "KOSDAQ")
    lst["Code"] = lst["Code"].astype(str)
    log("KR 종목 목록", len(lst))

    ysym = {c: c + (".KS" if m == "KOSPI" else ".KQ") for c, m in zip(lst["Code"], lst["mk"])}
    closes, vols = [], []
    syms = list(ysym.values())
    for i in range(0, len(syms), 400):  # 400개씩 나눠 받기
        chunk = syms[i:i + 400]
        for attempt in range(3):
            try:
                px = yf.download(chunk, period="13mo", interval="1d", auto_adjust=True, group_by="column",
                                 threads=True, progress=False)
                closes.append(px["Close"]); vols.append(px["Volume"])
                break
            except Exception as e:
                log("야후 실패, 다시 시도", i, e)
                time.sleep(10)
        log("KR 야후 시세", min(i + 400, len(syms)), "/", len(syms))
        time.sleep(2)
    C = pd.concat(closes, axis=1)
    V = pd.concat(vols, axis=1)
    C = C.loc[:, ~C.columns.duplicated()]
    V = V.loc[:, ~V.columns.duplicated()]

    fund = {}
    try:
        fund = naver_per_roe()
    except Exception as e:
        log("네이버 PER·ROE 실패(무시)", e)
    sectors, descs = kr_desc()

    rows, base = [], None
    for _, r in lst.iterrows():
        t, ys = r["Code"], ysym[r["Code"]]
        if ys not in C:
            continue
        s = C[ys].dropna()
        s = s[s > 0]
        if len(s) < 2:
            continue
        v = V[ys].reindex(s.index)
        p = round(float(s.iloc[-1]))
        base = max(base, s.index[-1]) if base is not None else s.index[-1]
        vol = num(v.iloc[-1], 0)
        stocks = num(r.get("Stocks"), 0)
        per, roe = fund.get(t, (None, None))
        pbr = round(per * roe / 100, 2) if per and roe and roe > 0 else None
        tk = tech(s, v)
        rows.append([
            t, r["Name"], r["mk"], (sectors.get(t) or "")[:20], p,
            round(p * stocks) if stocks else None,
            per, pbr, roe, None,
            vol, round(vol * p) if vol else None,
            *hist_returns(s),
            *[tk[k] for k in TECH_KEYS],
            None, None,
            tk["fl"] or 0,
            theme_bits(t, f"{descs.get(t, '')} {r['Name']}"),
        ])
    return rows, base.strftime("%Y-%m-%d") if base is not None else None


# ---------------------------------------------------------------- 미국
def sp500_list():
    try:
        import FinanceDataReader as fdr
        df = fdr.StockListing("S&P500")
        out = [(str(s), str(n), str(sec or "")) for s, n, sec in zip(df["Symbol"], df["Name"], df.get("Sector", [""] * len(df)))]
        if len(out) > 400:
            return out
    except Exception as e:
        log("FDR S&P500 목록 실패, 위키백과 사용", e)
    import requests
    html = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                        headers={"User-Agent": "stock-finder-bot/1.0"}, timeout=30).text
    df = pd.read_html(io.StringIO(html))[0]
    return [(str(s), str(n), str(sec)) for s, n, sec in zip(df["Symbol"], df["Security"], df["GICS Sector"])]


def fetch_us():
    import yfinance as yf

    fx = float(yf.Ticker("KRW=X").history(period="5d")["Close"].dropna().iloc[-1])
    log("USD/KRW", fx)

    lst = sp500_list()
    fix = {"BRKB": "BRK-B", "BFB": "BF-B"}  # 목록에 점 없이 들어오는 종목
    syms = [fix.get(s, s.replace(".", "-")) for s, _, _ in lst]  # BRK.B → BRK-B (야후 표기)
    log("S&P500", len(syms), "종목 시세 받는 중")
    px = yf.download(syms, period="13mo", interval="1d", auto_adjust=True, group_by="column",
                     threads=True, progress=False)
    close, volume = px["Close"], px["Volume"]
    base_date = None

    rows = []
    for i, ((_, name, sec), sym) in enumerate(zip(lst, syms)):
        try:
            s = close[sym].dropna()
            if len(s) < 2:
                continue
            last_d, last = s.index[-1], float(s.iloc[-1])
            base_date = max(base_date, last_d) if base_date is not None else last_d
            rr = hist_returns(s)
            v = volume[sym].dropna()
            vol = num(v.iloc[-1], 0) if len(v) else None
            tk = tech(s, v)

            info = {}
            for attempt in range(3):
                try:
                    info = yf.Ticker(sym).info or {}
                    break
                except Exception:
                    time.sleep(2 + attempt * 3)
            roe = num(info.get("returnOnEquity"))
            dr = num(info.get("dividendRate"))
            mc_usd = num(info.get("marketCap"), 0)
            rows.append([
                sym, name, "SP500", sec[:24], round(last, 2),
                round(mc_usd * fx) if mc_usd else None,
                pos(info.get("trailingPE")),
                pos(info.get("priceToBook")),
                round(roe * 100, 1) if roe is not None else None,
                round(dr / last * 100, 2) if dr else 0.0,
                vol,
                round(vol * last * fx) if vol else None,
                *rr,
                *[tk[k] for k in TECH_KEYS],
                None, None,  # 외국인·기관 수급은 국내만
                tk["fl"],
                theme_bits(sym, f"{sec} {info.get('sector', '')} {info.get('industry', '')}"),
            ])
        except Exception as e:
            log("US 실패", sym, e)
        if i % 50 == 0:
            log("US", i, "/", len(syms))
        time.sleep(0.25)
    return rows, base_date.strftime("%Y-%m-%d") if base_date is not None else None, round(fx, 2)


# ---------------------------------------------------------------- 메인
def main():
    old = {}
    if os.path.exists(OUT):
        try:
            old = json.load(open(OUT, encoding="utf-8"))
        except Exception:
            old = {}
    old_meta = old.get("meta", {}) if not old.get("meta", {}).get("sample") else {}
    old_rows = old.get("rows", []) if old.get("fields") == FIELDS and not old.get("meta", {}).get("sample") else []

    meta = {"updated": datetime.now(KST).isoformat(timespec="minutes"),
            "themes": [[tid, label] for tid, label, _, _ in THEMES]}
    ok = 0

    kr = []
    if os.environ.get("KRX_ID") and os.environ.get("KRX_PW"):
        try:
            kr, meta["base_kr"] = fetch_kr()
            if len(kr) < 1000:
                raise RuntimeError(f"국내 종목 수가 너무 적음: {len(kr)}")
            meta["kr_src"] = "krx"
            ok += 1
        except Exception:
            traceback.print_exc()
            kr = []
    else:
        log("KRX 로그인 정보 없음 → 로그인 없이 수집")
    if not kr:
        try:
            kr, meta["base_kr"] = fetch_kr_nologin()
            if len(kr) < 1000:
                raise RuntimeError(f"국내 종목 수가 너무 적음: {len(kr)}")
            meta["kr_src"] = "nologin"
            ok += 1
        except Exception:
            traceback.print_exc()
            kr = [r for r in old_rows if r[2] in ("KOSPI", "KOSDAQ")]
            meta["base_kr"] = old_meta.get("base_kr")
            meta["kr_src"] = old_meta.get("kr_src")
    log("KR rows", len(kr))

    try:
        us, meta["base_us"], meta["usdkrw"] = fetch_us()
        if len(us) < 300:
            raise RuntimeError(f"미국 종목 수가 너무 적음: {len(us)}")
        ok += 1
    except Exception:
        traceback.print_exc()
        us = [r for r in old_rows if r[2] == "SP500"]
        meta["base_us"], meta["usdkrw"] = old_meta.get("base_us"), old_meta.get("usdkrw")
    log("US rows", len(us))

    # 앱에 보여 줄 기준 시각: 장중에 돌렸으면 '장중', 아니면 '종가'
    now = datetime.now(KST)
    if meta.get("base_kr"):
        live = meta["base_kr"] == now.strftime("%Y-%m-%d") and (now.hour, now.minute) < (15, 40)
        meta["kr_time"] = now.isoformat(timespec="minutes") if live else meta["base_kr"]
        meta["kr_live"] = live
    if meta.get("base_us"):
        meta["us_time"], meta["us_live"] = meta["base_us"], False

    if not kr and not us:
        log("수집 전부 실패 - data.json을 바꾸지 않음")
        sys.exit(1)

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "fields": FIELDS, "rows": kr + us}, f, ensure_ascii=False, separators=(",", ":"))
    log("저장 완료", OUT, len(kr) + len(us), "종목", f"{os.path.getsize(OUT) / 1024:.0f}KB")
    if ok == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
