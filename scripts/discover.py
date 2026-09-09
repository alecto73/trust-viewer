#!/usr/bin/env python3
"""
FISIS 코드 탐색기 — 회사코드 / 통계표코드 / 계정항목코드를 직접 찾아본다.
지표를 추가하고 싶을 때 이 스크립트로 코드를 확인한 뒤 update_data.py의 MAP_* 에 넣으면 된다.

사용법:
  python scripts/discover.py companies                # 부동산신탁 14개사 코드
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
        for row in call("companySearch", partDiv="M")["list"]:
            mark = "  (폐업)" if "[폐]" in row["finance_nm"] else ""
            print(f'{row["finance_cd"]}  {row["finance_nm"]}{mark}')

    elif cmd == "tables":
        for sml, nm in (("A", "일반현황"), ("B", "재무현황"), ("E", "주요경영지표")):
            print(f"\n[{nm}]  lrgDiv=M smlDiv={sml}")
            for row in call("statisticsListSearch", lrgDiv="M", smlDiv=sml).get("list") or []:
                print(f'  {row["list_no"]}  {row["list_nm"]}')
        print("\n※ 신탁계정 통계는 공통(신탁) 권역: lrgDiv=B, smlDiv=E(부동산신탁)/F(신탁보수)")

    elif cmd == "accounts":
        list_no = args[1]
        needle = args[2] if len(args) > 2 else None
        for row in call("accountListSearch", listNo=list_no).get("list") or []:
            if needle and needle not in row["account_nm"]:
                continue
            print(f'{row["account_cd"]:8} {row["account_nm"]}')

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
