"""리뷰 한 건 또는 파일(한 줄에 한 리뷰)을 분류해 JSON으로 출력.

python scripts/classify.py "음식이 빨리 나오고 직원분들도 친절하세요"
python scripts/classify.py -f reviews.txt --backend hcx > out.jsonl
"""

import argparse
import json
from pathlib import Path

from naver_review_clf import get_backend

ap = argparse.ArgumentParser()
ap.add_argument("text", nargs="?")
ap.add_argument("-f", "--file")
ap.add_argument("--backend", default="baseline", choices=["baseline", "hcx"])
a = ap.parse_args()

clf = get_backend(a.backend)
texts = Path(a.file).read_text(encoding="utf-8").splitlines() if a.file else [a.text]
for t in texts:
    if t.strip():
        print(json.dumps(clf.classify(t).to_dict(), ensure_ascii=False))
