"""2일차: buyers 시트 정제 — workbench/day2/rules/정제규칙.md 1–7칸을 코드로 옮긴 것.

실행: uv run python scripts/clean_buyers.py
입력(읽기만): data/checkpoints/d2_start.xlsx (buyers · fx_at_ref), workbench/day2/data/fx_krw_daily.csv
출력: workbench/day2/outputs/d2_buyers_clean.csv, workbench/day2/outputs/dq_report.md
"""
import re
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

SRC = "data/checkpoints/d2_start.xlsx"
FX_DAILY = "workbench/day2/data/fx_krw_daily.csv"
OUT_DIR = Path("workbench/day2/outputs")
OUT_CSV = OUT_DIR / "d2_buyers_clean.csv"
OUT_MD = OUT_DIR / "dq_report.md"
ASOF = pd.Timestamp("2026-08-31")

AR_COLS = ["ar_balance", "ar_current", "ar_1_30", "ar_31_60", "ar_61_90", "ar_91p"]
BUCKETS = AR_COLS[1:]
FIN_COLS = ["current_assets", "current_liabilities", "total_liabilities", "equity",
            "accounts_receivable", "revenue", "operating_income"]
FIN_FLAG_COLS = ["current_assets", "current_liabilities", "total_liabilities", "equity", "revenue"]
DELAY_COLS = ["avg_days_late_12m", "avg_dpd_last3m", "avg_dpd_prev3m"]

# 1. 통화 매핑표 (공백 제거 · 대문자화 후 적용)
CCY_MAP = {"USD": "USD", "US$": "USD", "EUR": "EUR", "EURO": "EUR",
           "JPY": "JPY", "YEN": "JPY", "CNY": "CNY", "RMB": "CNY"}
# fx 열 이름과 단위(JPY는 100엔당)
FX_COL = {"USD": ("usd_krw", 1), "EUR": ("eur_krw", 1), "CNY": ("cny_krw", 1), "JPY": ("jpy100_krw", 100)}
FX_REF_CCY = {"USD": "USD", "EUR": "EUR", "CNY": "CNY", "JPY": "JPY100"}

OUT_COLS = ["buyer_id", "payment_method", "ccy_clean", "amt_num", "ar_balance_usd",
            "pct_current", "pct_ar_31p", "pct_91plus", "max_dpd", "avg_days_late",
            "late_ratio", "dpd_trend", "dispute_ratio", "current_ratio", "debt_to_equity",
            "dso_buyer", "op_margin", "fin_missing", "neg_equity", "country_risk",
            "crc_unclassified", "unit_flag", "fx_chg_30d", "fx_chg_30d_lag30", "late_30d"]


def is_missing(v):
    return v is None or (isinstance(v, float) and pd.isna(v)) or (isinstance(v, str) and v.strip() == "")


# 2. 숫자 변환: 공백 · 콤마 · 통화문자 제거, 끝의 k는 ×1000
CCY_TEXT = re.compile(r"(USD|US\$|EUR|EURO|JPY|YEN|CNY|RMB)", re.I)


def parse_amount(v):
    if is_missing(v):
        return np.nan
    if isinstance(v, (int, float)):
        return float(v)
    s = CCY_TEXT.sub("", str(v)).replace(",", "").strip()
    mult = 1
    if s.lower().endswith("k"):
        s, mult = s[:-1].strip(), 1000
    try:
        return float(s) * mult
    except ValueError:
        raise SystemExit(f"[멈춤] 숫자로 바꿀 수 없는 금액: {v!r}")


def profile(frame, cols):
    """전·후 비교용: 열별 결측 · 비숫자 · 표기 종류 수."""
    rows = []
    for c in cols:
        s = frame[c]
        miss = sum(is_missing(v) for v in s)
        nonnum = sum(1 for v in s if isinstance(v, str) and not is_missing(v))
        types = Counter(type(v).__name__ for v in s if not is_missing(v))
        rows.append((c, dict(types), miss, nonnum, s.nunique(dropna=True)))
    return rows


raw = pd.read_excel(SRC, sheet_name="buyers", dtype=object)
fx_ref = pd.read_excel(SRC, sheet_name="fx_at_ref")
fx_daily = pd.read_csv(FX_DAILY, parse_dates=["date"]).sort_values("date").set_index("date")
df = raw.copy()
log = []  # (buyer_id, 규칙, 원래 값, 바뀐 값)

# ---- 1. 통화 ----
ccy_key = df["currency"].astype(str).str.strip().str.upper()
unknown = sorted(set(ccy_key) - set(CCY_MAP))
if unknown:
    raise SystemExit(f"[멈춤] 매핑표에 없는 통화: {unknown}")
df["ccy_clean"] = ccy_key.map(CCY_MAP)

# ---- 2. 숫자 변환 ----
for c in AR_COLS:
    parsed = df[c].map(parse_amount)
    for b, before, after in zip(df.buyer_id, df[c], parsed):
        if not isinstance(before, (int, float)):
            log.append((b, "2. 숫자 변환", f"{c}={before!r}", f"{after:,.2f}".rstrip("0").rstrip(".")))
    df[c] = parsed
bucket_sum = df[BUCKETS].sum(axis=1)

# ---- 7-1. 기준일 환율 (USD 환산에 먼저 필요) ----
ref = fx_ref[fx_ref.ref_date.astype(str).str[:10] == "2026-08-31"].set_index("ccy")
krw_per_unit = {c: ref.loc[FX_REF_CCY[c], "krw_close"] / (100 if c == "JPY" else 1) for c in FX_COL}
to_usd = df["ccy_clean"].map(lambda c: krw_per_unit[c] / krw_per_unit["USD"])

# ---- 4. 단위 의심: 고치기 전 잔액을 USD로 바꿔 한도 × 10과 비교 ----
usd_before_fix = df["ar_balance"] * to_usd
df["unit_flag"] = (usd_before_fix > df["credit_limit_usd"].astype(float) * 10).astype(int)

# ---- 2 · 4. 잔액 ≠ 구간 합 → 구간 합 사용 (ar_fix) ----
diff = (df["ar_balance"] - bucket_sum).abs() > 0.005
ar_fix_rows = []
for i in df.index[diff]:
    b, before, after = df.at[i, "buyer_id"], df.at[i, "ar_balance"], bucket_sum[i]
    ratio = before / after if after else np.inf
    kind = "4. 단위 의심(×1000)" if df.at[i, "unit_flag"] and abs(ratio - 1000) < 1 else "2. 구간 합 사용"
    if df.at[i, "unit_flag"] and kind != "4. 단위 의심(×1000)":
        raise SystemExit(f"[멈춤] {b}: 단위 의심이지만 1000배가 아님(비율 {ratio:.2f}) — 사람 확인")
    log.append((b, kind, f"ar_balance={before:,.0f}", f"{after:,.0f}"))
    ar_fix_rows.append(b)
    df.at[i, "ar_balance"] = after
unit_unfixed = df.loc[(df.unit_flag == 1) & ~df.buyer_id.isin(ar_fix_rows), "buyer_id"].tolist()
if unit_unfixed:
    raise SystemExit(f"[멈춤] 단위 의심이지만 1000배 규칙에 맞지 않음: {unit_unfixed} — 사람 확인")

df["amt_num"] = df["ar_balance"]
df["ar_balance_usd"] = (df["amt_num"] * to_usd).round(2)

# ---- 3. 결측: 재무 7열 중위수 + fin_missing ----
for c in FIN_COLS + DELAY_COLS:
    df[c] = pd.to_numeric(df[c])
df["fin_missing"] = df[FIN_FLAG_COLS].isna().any(axis=1).astype(int)
medians = df[FIN_COLS].median()  # 채우기 전 150행 기준
fin_na_counts = df[FIN_COLS].isna().sum()
df[FIN_COLS] = df[FIN_COLS].fillna(medians)

# ---- 5. 자본잠식 ----
df["neg_equity"] = (df["equity"] <= 0).astype(int)

# ---- 6. 국가위험 ----
crc_raw = df["oecd_crc"]
df["crc_unclassified"] = crc_raw.map(lambda v: int(is_missing(v) or str(v).strip() == "-"))
df["country_risk"] = crc_raw.map(lambda v: 0 if is_missing(v) or str(v).strip() == "-" else int(v))
if not df["country_risk"].between(0, 7).all():
    raise SystemExit("[멈춤] oecd_crc 0~7 범위 밖 값 있음")

# ---- 7-2. 환율 변화율 ----
def asof(col, day):
    """day 당일 또는 직전 관측일 값."""
    return fx_daily.loc[:day, col].iloc[-1]


def chg30(col, day):
    return asof(col, day) / asof(col, day - pd.Timedelta(days=30)) - 1


lag_day = ASOF - pd.Timedelta(days=30)  # 2026-08-01
fx_chg = {c: (0.0 if c == "USD" else float(ref.loc[FX_REF_CCY[c], "chg_30d"])) for c in FX_COL}
fx_lag = {c: (0.0 if c == "USD" else round(chg30(FX_COL[c][0], lag_day), 4)) for c in FX_COL}
fx_check = {c: (float(ref.loc[FX_REF_CCY[c], "chg_30d"]), round(chg30(FX_COL[c][0], ASOF), 4)) for c in FX_COL}
df["fx_chg_30d"] = df["ccy_clean"].map(fx_chg)
df["fx_chg_30d_lag30"] = df["ccy_clean"].map(fx_lag)

# ---- 결과 열 계산 ----
amt = df["amt_num"]
safe = amt.where(amt > 0)
df["pct_current"] = (df["ar_current"] / safe).fillna(0).round(4)
df["pct_ar_31p"] = (df[["ar_31_60", "ar_61_90", "ar_91p"]].sum(axis=1) / safe).fillna(0).round(4)
df["pct_91plus"] = (df["ar_91p"] / safe).fillna(0).round(4)
df["avg_days_late"] = df["avg_days_late_12m"]
inv = df["invoices_12m"].astype(float).where(lambda s: s > 0)
df["late_ratio"] = (df["late_invoices_12m"] / inv).round(4)
df["dispute_ratio"] = (df["dispute_cnt_12m"] / inv).round(4)
df["dpd_trend"] = (df["avg_dpd_last3m"] - df["avg_dpd_prev3m"]).round(2)
df["current_ratio"] = (df["current_assets"] / df["current_liabilities"]).round(4)
df["debt_to_equity"] = (df["total_liabilities"] / df["equity"]).where(df["neg_equity"] == 0).round(4)
df["dso_buyer"] = (df["accounts_receivable"] / df["revenue"] * 365).round(1)
df["op_margin"] = (df["operating_income"] / df["revenue"]).round(4)

out = df[OUT_COLS].copy()

# ---- 검증 ----
checks = [
    ("행 수 150", len(out) == 150),
    ("buyer_id 첫 열 · 중복 없음", out.columns[0] == "buyer_id" and out.buyer_id.is_unique),
    ("late_30d 마지막 열 · 원본과 동일", out.columns[-1] == "late_30d"
     and (out.late_30d.astype(int).values == raw.late_30d.astype(int).values).all()),
    ("ccy_clean 4종", set(out.ccy_clean) <= {"USD", "EUR", "JPY", "CNY"}),
    ("amt_num = 구간 합", ((out.amt_num - bucket_sum).abs() < 0.005).all()),
    ("재무 비율에 무한대 없음", np.isfinite(out[["current_ratio", "dso_buyer", "op_margin"]]).all().all()),
    ("country_risk 0~7", out.country_risk.between(0, 7).all()),
]
if not all(ok for _, ok in checks):
    raise SystemExit(f"[멈춤] 검증 실패: {[n for n, ok in checks if not ok]}")

OUT_DIR.mkdir(parents=True, exist_ok=True)
out.to_csv(OUT_CSV, index=False, encoding="utf-8")

# ---- dq_report.md ----
L = ["# buyers 정제 전·후 비교 (DQ 리포트)", "",
     f"- 입력(읽기만): `{SRC}` · buyers 시트 / 환율: 같은 파일 `fx_at_ref` (ref_date 2026-08-31) · `{FX_DAILY}`",
     "- 규칙: `workbench/day2/rules/정제규칙.md` 1–7칸 / 코드: `scripts/clean_buyers.py`",
     f"- 출력: `{OUT_CSV.as_posix()}` ({out.shape[0]}행 × {out.shape[1]}열)", "",
     "## 1. 원천 열 전·후", "",
     "| 열 | 전: 타입 | 전: 결측 | 전: 문자 값 | 전: 표기 종류 | 후 |", "|---|---|---|---|---|---|"]
after_desc = {
    "currency": "ccy_clean 4종: " + ", ".join(f"{k} {v}" for k, v in out.ccy_clean.value_counts().items()),
    "oecd_crc": f"country_risk 정수 0~7 · crc_unclassified=1 {int(out.crc_unclassified.sum())}건",
    "ar_balance": f"amt_num 숫자 150 · 구간 합으로 교정 {len(ar_fix_rows)}건",
}
for c, types, miss, nonnum, nuniq in profile(raw, ["currency", "oecd_crc"] + AR_COLS + FIN_COLS + DELAY_COLS):
    if c in after_desc:
        after = after_desc[c]
    elif c in BUCKETS:
        after = "숫자 150"
    elif c in FIN_COLS:
        after = f"결측 0 (중위수 {medians[c]:,.0f}로 {fin_na_counts[c]}건 채움)"
    else:
        after = f"결측 {int(df[c].isna().sum())} 유지(규칙 3: 채우지 않음)"
    L.append(f"| {c} | {types} | {miss} | {nonnum} | {nuniq} | {after} |")

L += ["", "## 2. 바뀐 값 (행 단위)", "", "| buyer_id | 규칙 | 전 | 후 |", "|---|---|---|---|"]
L += [f"| {b} | {r} | `{x}` | {y} |" for b, r, x, y in log]

L += ["", "## 3. 플래그", "", "| 플래그 | 1인 건수 | 해당 buyer_id |", "|---|---|---|"]
for f in ["fin_missing", "neg_equity", "crc_unclassified", "unit_flag"]:
    ids = out.loc[out[f] == 1, "buyer_id"].tolist()
    L.append(f"| {f} | {len(ids)} | {', '.join(ids) if len(ids) <= 20 else '(' + str(len(ids)) + '건, CSV 참고)'} |")

L += ["", "## 4. 환율 (2026-08-31 기준)", "",
      "| 통화 | KRW/1단위 | → USD 계수 | fx_chg_30d | fx_chg_30d_lag30 | 검산: 일별 파일로 다시 낸 chg_30d |",
      "|---|---|---|---|---|---|"]
for c in FX_COL:
    L.append(f"| {c} | {krw_per_unit[c]:.4f} | {krw_per_unit[c] / krw_per_unit['USD']:.6f} | "
             f"{fx_chg[c]} | {fx_lag[c]} | {fx_check[c][1]} (fx_at_ref {fx_check[c][0]}) |")
L += ["", "- USD 청구는 규칙 7에 따라 변화율 0.",
      "- lag30 = (2026-08-01 직전 관측일 값) ÷ (2026-07-02 직전 관측일 값) − 1."]

L += ["", "## 5. 결과 열 결측", "", "| 열 | 결측 | 이유 |", "|---|---|---|"]
why = {"avg_days_late": "원천 지연 지표 빈칸 유지(규칙 3)",
       "late_ratio": "invoices_12m = 0 (분모 0)", "dispute_ratio": "invoices_12m = 0 (분모 0)",
       "dpd_trend": "avg_dpd_last3m 또는 prev3m 빈칸", "debt_to_equity": "자본잠식(규칙 5)"}
for c in OUT_COLS:
    n = int(out[c].isna().sum())
    if n:
        L.append(f"| {c} | {n} | {why.get(c, '확인 필요')} |")

L += ["", "## 6. 검증", "", "| 항목 | 결과 |", "|---|---|"]
L += [f"| {n} | {'통과' if ok else '실패'} |" for n, ok in checks]
OUT_MD.write_text("\n".join(L) + "\n", encoding="utf-8")

print(f"저장: {OUT_CSV} {out.shape}")
print(f"저장: {OUT_MD}")
for n, ok in checks:
    print(f"  {'OK ' if ok else 'NG '} {n}")
