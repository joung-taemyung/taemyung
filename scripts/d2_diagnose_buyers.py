"""2일차: d2_start.xlsx buyers 시트 진단(읽기만, 수정 없음).

실행: uv run python scripts/d2_diagnose_buyers.py
"""
import re
from collections import Counter

import pandas as pd

SRC = "data/checkpoints/d2_start.xlsx"
df = pd.read_excel(SRC, sheet_name="buyers", dtype=object)  # 원천 표기 그대로 읽기

AR_COLS = ["ar_balance", "ar_current", "ar_1_30", "ar_31_60", "ar_61_90", "ar_91p"]
CAT_COLS = ["country_code", "region", "payment_method", "oecd_crc", "currency"]
NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")


def is_missing(v):
    return v is None or (isinstance(v, float) and pd.isna(v)) or (isinstance(v, str) and v.strip() == "")


def to_num(v):
    """진단용 파싱: 순수 숫자/숫자 문자열만 숫자로, 나머지는 None."""
    if is_missing(v):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    return float(s) if NUM_RE.match(s) else None


print(f"shape={df.shape}\n")

# 1) 열별 타입 · 결측 · 고유값
print("## 열별 요약")
print("| 열 | 값 타입(파이썬) | 결측(NaN/빈칸) | 고유값 수 | 숫자로 안 읽히는 값 수 |")
print("|---|---|---|---|---|")
for c in df.columns:
    types = Counter(type(v).__name__ for v in df[c] if not is_missing(v))
    miss = sum(is_missing(v) for v in df[c])
    nonnum = sum(1 for v in df[c] if not is_missing(v) and to_num(v) is None)
    print(f"| {c} | {dict(types)} | {miss} | {df[c].nunique(dropna=True)} | {nonnum} |")

# 2) 범주형 표기 종류
print("\n## 범주형 표기 종류")
for c in CAT_COLS:
    vc = df[c].map(lambda v: "<결측>" if is_missing(v) else repr(v)).value_counts()
    print(f"- {c}: " + ", ".join(f"{k}={n}" for k, n in vc.items()))

# 3) 금액 열의 비숫자 표기
print("\n## 금액 열 비숫자 표기")
for c in AR_COLS:
    bad = [(b, v) for b, v in zip(df.buyer_id, df[c]) if not is_missing(v) and to_num(v) is None]
    print(f"- {c}: {len(bad)}건 {bad}")

# 4) 의심 값 규칙
print("\n## 의심 값")
num = df.copy()
for c in df.columns[6:]:
    num[c] = df[c].map(to_num)

def show(name, mask, cols):
    sub = df.loc[mask, ["buyer_id"] + cols]
    print(f"\n### {name}: {len(sub)}건")
    if len(sub):
        print(sub.to_string(index=False))

show("buyer_id 중복", df.buyer_id.duplicated(keep=False), [])
show("buyer_id 형식(B###) 아님", ~df.buyer_id.astype(str).str.fullmatch(r"B\d{3}"), [])
bucket_sum = num[AR_COLS[1:]].sum(axis=1, min_count=1)
show("잔액 ≠ 구간 합(둘 다 숫자일 때, 차이>1)",
     num.ar_balance.notna() & ((num.ar_balance - bucket_sum).abs() > 1), AR_COLS)
show("잔액 > 신용한도×10 (단위 의심, 사전 기준)",
     num.ar_balance > num.credit_limit_usd * 10, ["currency", "ar_balance", "credit_limit_usd"])
show("음수 금액/건수", (num[AR_COLS[1:] + ["max_dpd", "invoices_12m", "credit_limit_usd"]] < 0).any(axis=1), AR_COLS)
show("연체건 > 전체건", num.late_invoices_12m > num.invoices_12m, ["invoices_12m", "late_invoices_12m"])
show("분쟁건 > 전체건", num.dispute_cnt_12m > num.invoices_12m, ["invoices_12m", "dispute_cnt_12m"])
show("max_dpd ↔ 연체구간 불일치",
     ((num.max_dpd > 90) & ~(num.ar_91p > 0))
     | ((num.max_dpd == 0) & (num[AR_COLS[2:]].fillna(0).sum(axis=1) > 0)),
     ["max_dpd"] + AR_COLS[2:])
show("자기자본 ≤ 0 (자본잠식)", num.equity <= 0, ["equity", "total_liabilities"])
show("재무 항목 일부만 결측",
     num[df.columns[19:26]].isna().any(axis=1) & num[df.columns[19:26]].notna().any(axis=1),
     list(df.columns[19:26]))
show("재무 항목 전부 결측", num[df.columns[19:26]].isna().all(axis=1), ["revenue"])
show("매출채권 > 매출", num.accounts_receivable > num.revenue, ["accounts_receivable", "revenue"])
crc = df.oecd_crc.map(lambda v: None if is_missing(v) or v == "-" else to_num(v))
show("oecd_crc 0~7 범위 밖", crc.notna() & ~crc.between(0, 7), ["oecd_crc"])
show("late_30d ∉ {0,1}", ~num.late_30d.isin([0, 1]), ["late_30d"])

# 5) 국가 ↔ 권역 / 통화 일관성
print("\n## 국가별 권역 · 통화 조합")
print(df.groupby("country_code").agg(
    region=("region", lambda s: sorted(set(map(str, s)))),
    currency=("currency", lambda s: sorted(set(map(str, s)))),
    oecd_crc=("oecd_crc", lambda s: sorted(set(map(str, s)))),
    n=("buyer_id", "size")).to_string())
