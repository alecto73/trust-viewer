#!/usr/bin/env python3
"""data/fisis_data.json 을 index.html 안의 <script id="seed"> 에 다시 심는다.
Netlify에 올라간 사이트는 JSON을 fetch 하지만, 로컬에서 index.html 을 그냥
더블클릭해 열면(file://) fetch가 차단되므로 내장 스냅숏이 필요하다."""
import json, re, pathlib

root = pathlib.Path(__file__).resolve().parent.parent
data = json.loads((root / "data/fisis_data.json").read_text(encoding="utf-8"))
seed = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
html = (root / "index.html").read_text(encoding="utf-8")
new, n = re.subn(
    r'(<script id="seed" type="application/json">).*?(</script>)',
    lambda m: m.group(1) + seed + m.group(2),
    html, count=1, flags=re.S,
)
if not n:
    raise SystemExit('index.html 에서 <script id="seed"> 를 찾지 못했습니다.')
(root / "index.html").write_text(new, encoding="utf-8")
print(f"내장 스냅숏 갱신: {len(seed):,} bytes / 최신분기 {data['meta'].get('latest_quarter')}")
