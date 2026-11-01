#!/usr/bin/env python3
"""
FISIS 코드 탐색기 — 회사코드 / 통계표코드 / 계정항목코드를 직접 찾아본다.
지표를 추가하고 싶을 때 이 스크립트로 코드를 확인한 뒤 update_data.py의 MAP_* 에 넣으면 된다.

사용법:
  python scripts/discover.py companies                # 부동산신탁 14개사 코드
  python scripts/discover.py companies B              # 공통(신탁) 권역 회사코드(수탁고 SB008 등)
  python scripts/discover.py tables                   # 부동산신탁 통계표 전체
  python scripts/discover.py accounts SM005           # 통계표의 계정항목 전체
  python scripts/discover.py accounts SM005 신탁계정대   # 계정항목명 검색
  python scripts/discover.py peek 0010902 SM005 202603 # 실제 응답 원본 확인
"""

import json
import os
import sys
import urllib.parse
import urllib.request

AUTH = os.environ.get("FISIS_API_KEY", "").strip()
BASE = "https://fisis.fss.or.kr/openapi/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://fisis.fss.or.kr/page/api-test.jsp",
}


def call(service, **params):
    params.setdefault("lang", "kr")
    params["auth"] = AUTH
    url = BASE + service + ".json?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=40) as res:
        result = json.loads(res.read().decode("utf-8"))["result"]
    if result.get("err_cd") != "000":
        sys.exit(f"FISIS 오류 {result.get('err_cd')}: {result.get('err_msg')}")
    return result


def main():
    if not AUTH:
        sys.exit("FISIS_API_KEY 환경변수를 먼저 설정하세요.")
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    cmd = args[0]

    if cmd == "companies":
        part = args[1] if len(args) > 1 else "M"     # M=부동산신탁, B=공통(신탁) 등
        for row in call("companySearch", partDiv=part)["list"]:
            mark = "  (폐업)" if "[폐]" in row["finance_nm"] else ""
            print(f'{row["finance_cd"]}  {row["finance_nm"]}{mark}')

    elif cmd == "tables":
        groups = (
            ("M", "A", "부동산신탁 일반현황"), ("M", "B", "부동산신탁 재무현황"),
            ("M", "E", "부동산신탁 주요경영지표"),
            ("B", "E", "공통(신탁) 부동산신탁 — 수탁고 등 신탁계정"), ("B", "F", "공통(신탁) 신탁보수"),
        )
        for lrg, sml, nm in groups:
            print(f"\n[{nm}]  lrgDiv={lrg} smlDiv={sml}")
            for row in call("statisticsListSearch", lrgDiv=lrg, smlDiv=sml).get("list") or []:
                print(f'  {row["list_no"]}  {row["list_nm"]}')

    elif cmd == "accounts":
        list_no = args[1]
        needle = args[2] if len(args) > 2 else None
        for row in call("accountListSearch", listNo=list_no).get("list") or []:
            if needle and needle not in row["account_nm"]:
                continue
            print(f'{row["account_cd"]:8} {row["account_nm"]}')

    elif cmd == "multi":
        # 여러 명령을 ; 로 이어서 한 번에 실행 (Actions 탐색용)
        for part in " ".join(args[1:]).split(";"):
            sub = part.split()
            if sub:
                print(f"\n$ discover.py {' '.join(sub)}")
                sys.argv = [sys.argv[0]] + sub
                try:
                    main()
                except SystemExit as exc:
                    print(exc)

    elif cmd == "peek":
        finance_cd, list_no, base_mm = args[1], args[2], args[3]
        result = call(
            "statisticsInfoSearch",
            financeCd=finance_cd, listNo=list_no, term="Q",
            startBaseMm=base_mm, endBaseMm=base_mm,
        )
        print("컬럼:", json.dumps(result.get("description"), ensure_ascii=False))
        for row in result.get("list") or []:
            print(json.dumps(row, ensure_ascii=False))

    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
