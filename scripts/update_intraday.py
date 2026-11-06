"""
장중 가벼운 업데이트 (1시간마다)
- 열려 있는 시장만 현재가·거래량·하루 등락률을 새로 받는다
- 기간 상승률, PER·PBR·배당, 52주 고가/저가 대비, 20일선 이격도, 거래량 급증은
  아침 데이터의 기준값을 거꾸로 계산해 현재가로 다시 맞춘다 (재무 숫자·RSI·수급은 그대로)
- 결과는 data 브랜치에 최신 1개만 올린다 (저장소 용량이 쌓이지 않게)
"""
import json
import os
import sys
import warnings
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_data import FIELDS, KST, log, num  # noqa: E402

ET = ZoneInfo("America/New_York")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data.json")
I = {k: i for i, k in enumerate(FIELDS)}


def kr_open(now):
    k = now.astimezone(KST)
    return k.weekday() < 5 and (9, 0) <= (k.hour, k.minute) <= (16, 0)


def us_open(now):
    e = now.astimezone(ET)
    return e.weekday() < 5 and (9, 30) <= (e.hour, e.minute) <= (16, 30)


def apply(row, p_new, vol_new=None, val_new=None, r1d=None, mc_new=None):
    """아침 값과의 관계를 유지하면서 현재가로 다시 맞추기"""
    p_old = row[I["p"]]
    if not p_old or not p_new:
        return False
    k = p_new / p_old
    for f in ("r1w", "r1m", "r3m", "r6m", "r1y"):
        v = row[I[f]]
        if v is not None:
            row[I[f]] = round(((1 + v / 100) * k - 1) * 100, 1)
    if r1d is not None:
        row[I["r1d"]] = round(r1d, 1)
    for f in ("per", "pbr"):
        if row[I[f]]:
            row[I[f]] = round(row[I[f]] * k, 2)
    if row[I["div"]]:
        row[I["div"]] = round(row[I["div"]] / k, 2)
    if mc_new:
        row[I["mc"]] = round(mc_new)
    elif row[I["mc"]]:
        row[I["mc"]] = round(row[I["mc"]] * k)
    fl = row[I["fl"]] or 0
    h = row[I["h52"]]
    if h is not None:
        nh = (p_new / (p_old / (1 + h / 100)) - 1) * 100
        fl = (fl | 4) if nh >= 0 else (fl & ~4)
        row[I["h52"]] = round(min(nh, 0.0), 1)
    lo = row[I["l52"]]
    if lo is not None:
        nl = (p_new / (p_old / (1 + lo / 100)) - 1) * 100
        fl = (fl | 8) if nl <= 0 else (fl & ~8)
        row[I["l52"]] = round(max(nl, 0.0), 1)
    d = row[I["d20"]]
    if d is not None:
        row[I["d20"]] = round((p_new / (p_old / (1 + d / 100)) - 1) * 100, 1)
    if vol_new is not None:
        vr, vo = row[I["vr"]], row[I["vol"]]
        if vr and vo:
            row[I["vr"]] = round(vol_new / (vo / vr), 2)
        row[I["vol"]] = round(vol_new)
        if val_new is not None:
            row[I["val"]] = round(val_new)
    row[I["p"]] = round(p_new, 2) if row[I["m"]] == "SP500" else round(p_new)
    row[I["fl"]] = fl
    return True


def yahoo_now(syms, today):
    """야후 최근 5일 일봉 → {심볼: (현재가, 전일 종가, 거래량)} (오늘 봉이 있을 때만)"""
    import yfinance as yf
    out = {}
    for i in range(0, len(syms), 400):
        px = yf.download(syms[i:i + 400], period="5d", interval="1d", auto_adjust=True,
                         group_by="column", threads=True, progress=False)
        C, V = px["Close"], px["Volume"]
        for s in syms[i:i + 400]:
            if s not in C:
                continue
            c = C[s].dropna()
            if len(c) < 2 or c.index[-1].date() != today:
                continue
            v = V[s].reindex(c.index)
            out[s] = (float(c.iloc[-1]), float(c.iloc[-2]), num(v.iloc[-1], 0))
    return out


def update_kr(rows):
    today = datetime.now(KST)
    n = 0
    if os.environ.get("KRX_ID") and os.environ.get("KRX_PW"):
        try:
            from pykrx import stock
            d = today.strftime("%Y%m%d")
            df = stock.get_market_ohlcv(d, market="ALL")  # 시가 고가 저가 종가 거래량 거래대금 등락률
            if df.empty or df["종가"].sum() == 0:
                log("KR 오늘 시세 없음 (휴장일?)")
                return 0
            cap = {}
            try:
                cap = stock.get_market_cap(d, market="ALL")["시가총액"].to_dict()
            except Exception as e:
                log("KR 시가총액 실패(무시)", e)
            for r in rows:
                if r[I["m"]] == "SP500" or r[I["t"]] not in df.index:
                    continue
                x = df.loc[r[I["t"]]]
                if apply(r, num(x["종가"], 0), num(x["거래량"], 0), num(x["거래대금"], 0),
                         num(x["등락률"], 2), num(cap.get(r[I["t"]]), 0)):
                    n += 1
            return n
        except Exception as e:
            log("KR pykrx 실패 → 야후로", e)
    rows_kr = [r for r in rows if r[I["m"]] != "SP500"]
    ys = {r[I["t"]] + (".KS" if r[I["m"]] == "KOSPI" else ".KQ"): r for r in rows_kr}
    got = yahoo_now(list(ys), today.date())
    for s, (p, prev, vol) in got.items():
        if apply(ys[s], p, vol, vol * p if vol else None, (p / prev - 1) * 100):
            n += 1
    return n


def update_us(rows, fx):
    today = datetime.now(ET).date()
    ys = {r[I["t"]]: r for r in rows if r[I["m"]] == "SP500"}
    got = yahoo_now(list(ys), today)
    n = 0
    for s, (p, prev, vol) in got.items():
        if apply(ys[s], p, vol, vol * p * fx if vol and fx else None, (p / prev - 1) * 100):
            n += 1
    return n


def main():
    d = json.load(open(OUT, encoding="utf-8"))
    if d.get("fields") != FIELDS or d.get("meta", {}).get("sample"):
        log("data.json 형식이 달라서 건너뜀")
        return
    rows, meta = d["rows"], d["meta"]
    now = datetime.now(KST)
    stamp = now.isoformat(timespec="minutes")
    changed = False
    if kr_open(now) or os.environ.get("FORCE_KR"):
        n = update_kr(rows)
        log("KR 장중 업데이트", n)
        if n > 500:
            meta["kr_time"], meta["kr_live"] = stamp, True
            changed = True
    if us_open(now) or os.environ.get("FORCE_US"):
        n = update_us(rows, meta.get("usdkrw"))
        log("US 장중 업데이트", n)
        if n > 200:
            meta["us_time"], meta["us_live"] = stamp, True
            changed = True
    if not changed:
        log("열린 시장이 없거나 바뀐 값 없음")
        return
    meta["updated"] = stamp
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, separators=(",", ":"))
    log("저장 완료")


if __name__ == "__main__":
    main()
