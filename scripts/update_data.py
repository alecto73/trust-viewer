#!/usr/bin/env python3
"""
FISIS(금융감독원 금융통계정보시스템) 부동산신탁사 재무지표 수집기

실제 오픈API 규격 (fisis.fss.or.kr/page/api-spec.jsp 기준)
  - 통계정보:  GET /openapi/statisticsInfoSearch.json
               auth, financeCd, listNo, [accountCd], lang, term, startBaseMm, endBaseMm
  - 금융회사:  GET /openapi/companySearch.json        (partDiv=M → 부동산신탁)
  - 통계목록:  GET /openapi/statisticsListSearch.json (lrgDiv=M, smlDiv=A/B/E)
  - 계정항목:  GET /openapi/accountListSearch.json    (listNo)

주의: http:// 로 호출하면 307 리다이렉트가 발생하므로 https:// 를 직접 쓴다.
      기본 UA로는 503이 떨어지므로 브라우저 User-Agent를 붙인다.
      조회 기간은 40분기를 초과할 수 없다(err_cd 103).
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

AUTH = os.environ.get("FISIS_API_KEY", "").strip()
BASE = "https://fisis.fss.or.kr/openapi/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://fisis.fss.or.kr/page/api-test.jsp",
}

KEEP_QUARTERS = int(os.environ.get("FISIS_KEEP_QUARTERS", "12"))
# 표시할 12분기 + ROE/ROA의 4분기 롤링에 필요한 앞선 3분기 + 공시 지연 여유 2분기
FETCH_QUARTERS = KEEP_QUARTERS + 5


def quarter_ends(count, ref=None):
    """현재 분기말부터 거꾸로 count개의 결산년월(YYYYMM)을 오름차순으로 반환."""
    now = ref or datetime.now()
    year, month = now.year, ((now.month - 1) // 3) * 3 + 3
    out = []
    while len(out) < count:
        out.append(f"{year}{month:02d}")
        month -= 3
        if month < 1:
            month, year = 12, year - 1
    return list(reversed(out))


_window = quarter_ends(FETCH_QUARTERS)
# 조회 구간: 넉넉히 요청하고, 실제 응답에 존재하는 분기만 사용한다.
START_MM = os.environ.get("FISIS_START_MM") or _window[0]
END_MM = os.environ.get("FISIS_END_MM") or _window[-1]

OUT_PATH = os.environ.get("FISIS_OUT", "data/fisis_data.json")

# 통계표
TB_BS = "SM005"      # 요약재무상태표(고유계정, '11.03월 이후)
TB_PL = "SM007"      # 요약손익계산서(고유계정, '11.03월 이후)
TB_NCR = "SM009"     # 자본적정성(영업용순자본비율)
TB_NPL = "SM013"     # 자산건전성(고정이하자산비율)
TB_CLASS = "SM016"   # 자산건전성 분류(자산유형 × 정상~추정손실)

# 계정항목코드 → 내부 필드명
MAP_BS = {
    "A": "total_assets",            # 자산총계
    "A1": "cash_and_deposits",      # Ⅰ. 현금 및 예치금
    "A4": "loans_gross",            # Ⅳ. 대출채권
    "A43": "loan_lending",          # 대여금
    "A44": "loan_advance",          # 대지급금
    "A45": "trust_account_loan",    # 신탁계정대  ★
    "A47": "allowance_loans",       # 대출채권 대손충당금 계
    "A472": "allowance_tal",        # 신탁계정대 대손충당금 ★
    "B": "total_liabilities",       # 부채총계
    "B2": "borrowings",             # Ⅱ. 차입부채
    "C": "total_equity",            # 자본총계
    "C1": "capital_stock",          # 자본금
}
MAP_PL = {
    "A": "operating_revenue",       # Ⅰ. 영업수익
    "A1": "fee_income",             # 수수료수익
    "A11": "trust_fee",             # 신탁보수 ★
    "C": "operating_income",        # Ⅲ. 영업이익 계열(응답 계정명으로 검증)
    "G": "net_income",              # Ⅹ. 당기순이익(손실) ★
}
MAP_NCR = {
    "A": "net_operating_capital",   # 영업용순자본
    "B": "total_risk",              # 총위험액
    "C": "ncr",                     # 영업용순자본비율 (%) ★
}
MAP_NPL = {
    "A": "substandard_below",       # 고정이하자산
    "B": "classified_assets",       # 건전성 분류대상 자산
    "C": "substandard_below_ratio", # 고정이하자산비율 (%) ★
}

TIMEOUT = 40
RETRY = 3


def call(service, **params):
    params.setdefault("lang", "kr")
    params["auth"] = AUTH
    url = BASE + service + ".json?" + urllib.parse.urlencode(params)
    last = None
    for attempt in range(RETRY):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
                body = json.loads(res.read().decode("utf-8"))
            result = body.get("result", {})
            code = result.get("err_cd")
            if code != "000":
                raise RuntimeError(f"FISIS err_cd={code} {result.get('err_msg')}")
            return result
        except Exception as exc:          # noqa: BLE001
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{service} 호출 실패: {last}")


def to_float(value):
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def fetch_table(finance_cd, list_no, field_map):
    """통계표 1개를 조회 구간 통째로 받아 {base_month: {field: value}} 로 정리."""
    result = call(
        "statisticsInfoSearch",
        financeCd=finance_cd,
        listNo=list_no,
        term="Q",
        startBaseMm=START_MM,
        endBaseMm=END_MM,
    )
    out = {}
    for row in result.get("list") or []:
        field = field_map.get(row.get("account_cd"))
        if not field:
            continue
        out.setdefault(row["base_month"], {})[field] = to_float(row.get("a"))
    return out


def fetch_tal_classification(finance_cd):
    """SM016에서 신탁계정대(계정 K)의 건전성 등급별 분포를 뽑는다."""
    result = call(
        "statisticsInfoSearch",
        financeCd=finance_cd,
        listNo=TB_CLASS,
        term="Q",
        startBaseMm=START_MM,
        endBaseMm=END_MM,
    )
    out = {}
    for row in result.get("list") or []:
        if row.get("account_cd") != "K":       # K = 신탁계정대
            continue
        normal = to_float(row.get("a")) or 0.0
        watch = to_float(row.get("b")) or 0.0
        substd = to_float(row.get("c")) or 0.0
        doubtful = to_float(row.get("d")) or 0.0
        loss = to_float(row.get("e")) or 0.0
        total = to_float(row.get("f")) or (normal + watch + substd + doubtful + loss)
        out[row["base_month"]] = {
            "tal_normal": normal,
            "tal_watch": watch,
            "tal_substandard": substd,
            "tal_doubtful": doubtful,
            "tal_loss": loss,
            "tal_classified_total": total,
            "tal_substandard_below": substd + doubtful + loss,
        }
    return out


def pct(numerator, denominator, digits=2):
    if numerator is None or not denominator:
        return None
    return round(numerator / denominator * 100, digits)


EOK = 100_000_000  # 억원


def eok(value, digits=1):
    if value is None:
        return None
    return round(value / EOK, digits)


def derive(raw):
    """원천 계정 → 화면에서 쓰는 지표. 금액은 억원 단위로 환산."""
    ta = raw.get("total_assets")
    eq = raw.get("total_equity")
    li = raw.get("total_liabilities")
    tal = raw.get("trust_account_loan")
    allow_tal = raw.get("allowance_tal")
    borrow = raw.get("borrowings")
    cash = raw.get("cash_and_deposits")
    net = raw.get("net_income")
    tal_sub = raw.get("tal_substandard_below")
    tal_total = raw.get("tal_classified_total")

    net_borrow = None
    if borrow is not None:
        net_borrow = borrow - (cash or 0.0)

    return {
        # 평균(업계 합산) 계산용 원천 분자·분모
        "total_liabilities": eok(li),
        "cash_and_deposits": eok(cash),
        "net_borrowings": eok(net_borrow),
        "net_operating_capital": eok(raw.get("net_operating_capital")),
        "total_risk": eok(raw.get("total_risk")),
        "tal_substandard_below": eok(tal_sub),
        "tal_classified_total": eok(tal_total),
        "substandard_below": eok(raw.get("substandard_below")),
        "classified_assets": eok(raw.get("classified_assets")),
        # 규모 (억원)
        "total_assets": eok(ta),
        "total_equity": eok(eq),
        "capital_stock": eok(raw.get("capital_stock")),
        "net_income": eok(net),
        "operating_revenue": eok(raw.get("operating_revenue")),
        "trust_fee": eok(raw.get("trust_fee")),
        "trust_account_loan": eok(tal),
        "allowance_tal": eok(allow_tal),
        "borrowings": eok(borrow),
        # 건전성
        "ncr": raw.get("ncr"),
        "substandard_below_ratio": raw.get("substandard_below_ratio"),
        "tal_substandard_ratio": pct(tal_sub, tal_total),
        # 리스크 파생
        "tal_to_equity": pct(tal, eq),
        "allowance_coverage": pct(allow_tal, tal),
        "debt_ratio": pct(li, eq),
        "borrow_dependency": pct(borrow, ta),
        "net_borrow_dependency": pct(net_borrow, ta),
        # 수익성: roa/roe는 해당 분기 단독, roa_ttm/roe_ttm은 4분기 롤링(권장)
        "roa": pct(net, ta),
        "roe": pct(net, eq),
        "trust_fee_share": pct(raw.get("trust_fee"), raw.get("operating_revenue")),
    }


def add_rolling(series):
    """
    FISIS 요약손익계산서의 분기 수치는 '누적'이 아니라 '해당 분기 단독 발생액'이다.
    (검증: 무궁화신탁 2025 Q1 -60억 → Q2 -447억 → Q3 -146억, 누적이면 불가능한 흐름)
    따라서 수익성 지표는 연환산이 아니라 4분기 롤링 합계(TTM)로 계산한다.
    series: {base_month: derived_dict} — base_month 오름차순 처리
    """
    months = sorted(series)
    for i, month in enumerate(months):
        cur = series[month]
        window = months[max(0, i - 3):i + 1]
        if len(window) < 4:
            cur["net_income_ttm"] = None
            cur["operating_revenue_ttm"] = None
            cur["roa_ttm"] = None
            cur["roe_ttm"] = None
            continue
        net_sum = sum((series[m].get("net_income") or 0.0) for m in window)
        rev_sum = sum((series[m].get("operating_revenue") or 0.0) for m in window)
        ta = cur.get("total_assets")
        eq = cur.get("total_equity")
        cur["net_income_ttm"] = round(net_sum, 1)
        cur["operating_revenue_ttm"] = round(rev_sum, 1)
        cur["roa_ttm"] = pct(net_sum, ta)
        cur["roe_ttm"] = pct(net_sum, eq)
    return series


def label_of(base_month):
    year, month = base_month[:4], base_month[4:]
    return f"{year}Q{ {'03': 1, '06': 2, '09': 3, '12': 4}[month] }"


def guard(payload):
    """무인 실행에서 빈 데이터가 기존 파일을 덮어쓰는 사고를 막는다."""
    quarters = payload["quarters"]
    if not quarters:
        sys.exit("중단: 사용 가능한 분기가 없습니다. 기존 데이터를 유지합니다.")

    latest = quarters[-1]
    filled = sum(
        1 for code in payload["data"]
        if (payload["data"][code].get(latest) or {}).get("total_assets") is not None
    )
    total = len(payload["companies"])
    if filled < (total + 1) // 2:
        sys.exit(f"중단: 최신 분기({latest}) 수집률 {filled}/{total}. 기존 데이터를 유지합니다.")

    if not os.path.exists(OUT_PATH):
        return
    try:
        with open(OUT_PATH, encoding="utf-8") as fp:
            prev = json.load(fp)
    except Exception:      # noqa: BLE001
        return
    prev_q = prev.get("quarters") or []
    if prev_q and latest < prev_q[-1]:
        sys.exit(f"중단: 수집된 최신 분기({latest})가 기존 파일({prev_q[-1]})보다 과거입니다.")
    if len(quarters) < len(prev_q):
        sys.exit(f"중단: 분기 수가 {len(prev_q)}개에서 {len(quarters)}개로 줄었습니다.")
    if prev_q and latest == prev_q[-1]:
        print(f"참고: 최신 분기는 {latest}로 이전과 동일합니다. 수치 정정만 반영됩니다.")


def main():
    if not AUTH:
        sys.exit("FISIS_API_KEY 환경변수가 비어 있습니다. 인증키를 설정한 뒤 다시 실행하세요.")

    print("부동산신탁사 목록 조회 (partDiv=M)")
    companies = []
    for row in call("companySearch", partDiv="M").get("list") or []:
        name = row["finance_nm"]
        if "[폐]" in name:            # 폐업사 제외
            continue
        companies.append({"code": row["finance_cd"], "name": name})
    companies.sort(key=lambda c: c["name"])
    print(f"  대상 {len(companies)}개사")

    dataset = {}
    all_months = set()

    for idx, comp in enumerate(companies, 1):
        code = comp["code"]
        print(f"[{idx}/{len(companies)}] {comp['name']} ({code})")
        merged = {}
        for list_no, field_map in (
            (TB_BS, MAP_BS),
            (TB_PL, MAP_PL),
            (TB_NCR, MAP_NCR),
            (TB_NPL, MAP_NPL),
        ):
            try:
                for month, values in fetch_table(code, list_no, field_map).items():
                    merged.setdefault(month, {}).update(values)
            except Exception as exc:      # noqa: BLE001
                print(f"    - {list_no} 건너뜀: {exc}")
            time.sleep(0.3)
        try:
            for month, values in fetch_tal_classification(code).items():
                merged.setdefault(month, {}).update(values)
        except Exception as exc:          # noqa: BLE001
            print(f"    - {TB_CLASS} 건너뜀: {exc}")
        time.sleep(0.3)

        dataset[code] = add_rolling({m: derive(v) for m, v in merged.items()})
        all_months.update(merged.keys())
        latest = max(merged) if merged else "없음"
        print(f"    분기 {len(merged)}개 수집, 최신 {latest}")

    # 공시 지연으로 일부 회사만 올라온 분기는 제외한다.
    # 자산총계가 채워진 회사가 과반이 아니면 아직 미완성 분기로 본다.
    need = max(2, (len(companies) + 1) // 2)
    usable = []
    for month in sorted(all_months):
        filled = sum(
            1 for c in companies
            if (dataset.get(c["code"], {}).get(month) or {}).get("total_assets") is not None
        )
        if filled >= need:
            usable.append(month)
        else:
            print(f"  분기 {label_of(month)} 제외: {filled}/{len(companies)}개사만 수집됨")

    months = usable[-KEEP_QUARTERS:]
    quarters = [label_of(m) for m in months]
    if len(months) < KEEP_QUARTERS:
        print(f"\n경고: 사용 가능한 분기가 {len(months)}개뿐입니다 (목표 {KEEP_QUARTERS}개)")

    payload = {
        "meta": {
            "source": "금융감독원 금융통계정보시스템(FISIS) 오픈API",
            "generated_at": datetime.now(KST).replace(tzinfo=None).isoformat(timespec="seconds"),
            "unit": "금액=억원, 비율=%",
            "basis": "고유계정 기준 (신탁계정 별도)",
            "tables": [TB_BS, TB_PL, TB_NCR, TB_NPL, TB_CLASS],
            "latest_quarter": quarters[-1] if quarters else None,
        },
        "quarters": quarters,
        "companies": companies,
        "data": {
            code: {label_of(m): dataset[code].get(m, {}) for m in months}
            for code in dataset
        },
    }

    guard(payload)

    # 수집 시각(generated_at)만 다르고 수치가 같으면 파일을 그대로 둔다.
    # 이렇게 해야 워크플로의 git diff 검사가 "변경 없음"을 인식해 빈 커밋·재배포가 생기지 않는다.
    if os.path.exists(OUT_PATH):
        try:
            with open(OUT_PATH, encoding="utf-8") as fp:
                prev = json.load(fp)
        except Exception:      # noqa: BLE001
            prev = None

        def strip(d):
            meta = {k: v for k, v in (d.get("meta") or {}).items() if k != "generated_at"}
            return {**d, "meta": meta}

        if prev is not None and strip(prev) == strip(payload):
            print(f"\n변경 없음: 수치가 기존 파일과 같아 {OUT_PATH} 를 유지합니다.")
            return

    os.makedirs(os.path.dirname(OUT_PATH) or ".", exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=1)
    print(f"\n저장 완료: {OUT_PATH}  ({len(companies)}개사 × {len(quarters)}분기)")
    print(f"수집 분기: {', '.join(quarters)}")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fp:
            fp.write(f"### FISIS 수집 결과\n\n")
            fp.write(f"- 조회 창: {START_MM} ~ {END_MM}\n")
            fp.write(f"- 대상: {len(companies)}개사\n")
            fp.write(f"- 표시 분기: {quarters[0]} ~ {quarters[-1]} ({len(quarters)}개)\n")


if __name__ == "__main__":
    main()
