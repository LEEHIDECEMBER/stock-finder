"""
선호 주식 찾기 - 데이터 수집 스크립트
GitHub Actions가 하루 한 번 실행해서 저장소 맨 위의 data.json을 새로 만든다.

- 국내(코스피·코스닥): pykrx (KRX 정보데이터시스템)
    · 기준일 시세·시총·PER/PBR/EPS/BPS/배당, 기간 등락률
    · 최근 약 1년치 일별 종가·거래량 → 추세 지표 계산
    · 최근 5거래일 외국인·기관 순매수
  pykrx가 막히면 FinanceDataReader로 시세만
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
from datetime import datetime, timedelta, timezone

import pandas as pd

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
    ("auto", "자동차", ["자동차", "Auto Manufacturers", "Auto Parts", "Automobile"], []),
    ("battery", "2차전지", ["이차전지", "2차전지", "축전지", "배터리", "양극재", "음극재", "전해액", "분리막", "리튬", "Lithium"], ["ALB"]),
    ("bio", "바이오·헬스케어", ["의약", "바이오", "제약", "의료", "진단", r"Health ?care", "Biotech", "Drug", "Medical", "Diagnostics"], []),
    ("soft", "소프트웨어·인터넷", ["소프트웨어", "포털", "인터넷", "플랫폼", "클라우드", "Software", "Internet", "Information Technology Services"], []),
    ("game", "게임·엔터", ["게임", "엔터테인먼트", "음반", "오디오물", "영화", "방송 프로그램", "Entertainment", "Electronic Gaming", "Broadcasting"], []),
    ("fin", "금융", ["은행", "증권", "보험", "신용카드", "여신", "저축기관", "금융 지원", "Financial", "Bank", "Insurance", "Capital Markets"], []),
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


# ---------------------------------------------------------------- 국내
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

    sectors, descs = {}, {}
    try:
        import FinanceDataReader as fdr
        desc = fdr.StockListing("KRX-DESC")
        codes = desc["Code"].astype(str)
        sectors = dict(zip(codes, desc["Sector"].fillna("")))
        descs = dict(zip(codes, desc["Sector"].fillna("") + " " + desc.get("Industry", pd.Series("", index=desc.index)).fillna("")))
    except Exception as e:
        log("KR 업종 정보 실패(무시)", e)

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


def fetch_kr_fallback():
    """pykrx가 막혔을 때: FinanceDataReader로 시세·시총·거래량·하루 등락률만"""
    import FinanceDataReader as fdr
    df = fdr.StockListing("KRX")
    rows = []
    for _, r in df.iterrows():
        mk = str(r.get("Market", "")).upper()
        mk = "KOSDAQ" if mk.startswith("KOSDAQ") else "KOSPI" if mk == "KOSPI" else None
        p = num(r.get("Close"), 0)
        if not mk or not p:
            continue
        row = [None] * len(FIELDS)
        row[:12] = [str(r["Code"]), r["Name"], mk, "", p, num(r.get("Marcap"), 0), None, None, None, None,
                    num(r.get("Volume"), 0), num(r.get("Amount"), 0)]
        row[FIELDS.index("r1d")] = num(r.get("ChagesRatio", r.get("ChangesRatio")), 1)
        row[FIELDS.index("fl")] = 0
        row[FIELDS.index("th")] = theme_bits(str(r["Code"]), str(r["Name"]))
        rows.append(row)
    return rows, datetime.now(KST).strftime("%Y-%m-%d")


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
    syms = [s.replace(".", "-") for s, _, _ in lst]  # BRK.B → BRK-B (야후 표기)
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
            rr = [round((last / float(s.iloc[-2]) - 1) * 100, 1)]  # 하루
            for days in WINDOWS.values():
                past = s[s.index <= last_d - pd.Timedelta(days=days)]
                rr.append(round((last / float(past.iloc[-1]) - 1) * 100, 1) if len(past) else None)
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

    try:
        kr, meta["base_kr"] = fetch_kr()
        if len(kr) < 1000:
            raise RuntimeError(f"국내 종목 수가 너무 적음: {len(kr)}")
        ok += 1
    except Exception:
        traceback.print_exc()
        try:
            kr, meta["base_kr"] = fetch_kr_fallback()
            meta["kr_note"] = "pykrx 실패 → 시세만 수집"
            ok += 1
        except Exception:
            traceback.print_exc()
            kr = [r for r in old_rows if r[2] in ("KOSPI", "KOSDAQ")]
            meta["base_kr"] = old_meta.get("base_kr")
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
