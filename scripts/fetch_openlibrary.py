# scripts/generate/_books_openlibrary_fill.py
import re
import time
import json
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

import requests
import yaml

# ========= Root autodetect (兼容 scripts/ 和 scripts/generate/) =========
here = Path(__file__).resolve()
cand1 = here.parents[1]  # 脚本在 scripts/generate/ -> parents[1] = scripts
cand2 = here.parents[2]  # 脚本在 scripts/generate/ -> parents[2] = 项目根

def is_root(p: Path) -> bool:
    return (p / "content").exists() and (p / "layouts").exists() and (p / "hugo.toml").exists()

ROOT = cand1 if is_root(cand1) else cand2
BOOK_DIR = ROOT / "content" / "books"

SEARCH_API = "https://openlibrary.org/search.json"
WORK_API_PREFIX = "https://openlibrary.org"
COVER_PREFIX = "https://covers.openlibrary.org/b/id"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "wuwu-books/1.0 (OpenLibrary lookup; contact: none)"
})

# 策略
FILL_MISSING_ONLY = True   # True: 只补缺失字段；False: 会覆盖现有字段
WRITE_COVER = True         # True: 写入 cover 字段
SLEEP_SEC = 0.25           # 请求间隔


def read_front_matter(md_text: str) -> Tuple[Dict[str, Any], str]:
    """Return (front_matter_dict, body_text) for YAML front matter delimited by ---."""
    m = re.match(r"(?s)^---\s*\n(.*?)\n---\s*\n?(.*)$", md_text)
    if not m:
        return {}, md_text
    fm_raw, body = m.group(1), m.group(2)
    fm = yaml.safe_load(fm_raw) or {}
    return fm, body


def write_markdown(path: Path, fm: Dict[str, Any], body: str) -> None:
    fm_text = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()
    out = f"---\n{fm_text}\n---\n\n{body.lstrip()}"
    path.write_text(out, encoding="utf-8")


def ol_search(title: str, author: Optional[str], q_fallback: bool = True) -> Optional[Dict[str, Any]]:
    """
    Prefer title/author params (更稳定，避免中文 q= 422).
    Fallback to q= only if needed.
    """
    # 1) Prefer structured fields
    params = {"title": title, "limit": 10}
    if author:
        params["author"] = author

    r = SESSION.get(SEARCH_API, params=params, timeout=20)

    # 2) Rare fallback (some edge cases)
    if r.status_code == 422 and q_fallback:
        params2 = {"q": title, "limit": 10}
        if author:
            params2["author"] = author
        r = SESSION.get(SEARCH_API, params=params2, timeout=20)

    r.raise_for_status()
    data = r.json()
    docs = data.get("docs") or []
    if not docs:
        return None

    def score(d: Dict[str, Any]) -> int:
        s = 0
        if d.get("key"): s += 3
        if d.get("cover_i"): s += 2
        if d.get("author_name"): s += 2
        if d.get("first_publish_year"): s += 1
        if d.get("title"): s += 1
        return s

    docs.sort(key=score, reverse=True)
    return docs[0]


def ol_work_detail(work_key: str) -> Optional[Dict[str, Any]]:
    url = f"{WORK_API_PREFIX}{work_key}.json"  # work_key like "/works/OLxxxxW"
    r = SESSION.get(url, timeout=20)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def extract_description(work: Dict[str, Any]) -> Optional[str]:
    desc = work.get("description")
    if isinstance(desc, str):
        return desc.strip()
    if isinstance(desc, dict) and isinstance(desc.get("value"), str):
        return desc["value"].strip()
    return None


def extract_cover_id(search_doc: Dict[str, Any], work: Optional[Dict[str, Any]]) -> Optional[int]:
    if search_doc.get("cover_i"):
        return int(search_doc["cover_i"])
    if work and isinstance(work.get("covers"), list) and work["covers"]:
        try:
            return int(work["covers"][0])
        except Exception:
            return None
    return None


def pick_author(search_doc: Dict[str, Any]) -> Optional[str]:
    names = search_doc.get("author_name")
    if isinstance(names, list) and names:
        return str(names[0]).strip()
    return None


def update_one(md_path: Path) -> Dict[str, Any]:
    raw = md_path.read_text(encoding="utf-8")
    fm, body = read_front_matter(raw)

    # ✅ 优先用你手动提供的 OpenLibrary 检索字段
    title = (fm.get("ol_title") or fm.get("title") or "").strip()
    author = (fm.get("ol_author") or fm.get("author") or "").strip() or None

    # 这两个是要补全的字段
    existing_author = (fm.get("author") or "").strip()
    existing_summary = (fm.get("summary") or "").strip()

    if not title:
        return {"file": md_path.name, "status": "skip", "reason": "missing title/ol_title"}

    doc = ol_search(title=title, author=author)
    time.sleep(SLEEP_SEC)

    if not doc or not doc.get("key"):
        return {"file": md_path.name, "status": "not_found"}

    work_key = doc["key"]
    work = ol_work_detail(work_key)
    time.sleep(SLEEP_SEC)

    ol_desc = extract_description(work) if work else None
    ol_author = pick_author(doc)
    cover_id = extract_cover_id(doc, work)
    cover_url = f"{COVER_PREFIX}/{cover_id}-L.jpg" if cover_id else None

    changed: Dict[str, Any] = {}

    # author：缺失才补（或允许覆盖）
    if ol_author and (not existing_author or not FILL_MISSING_ONLY):
        if fm.get("author") != ol_author:
            fm["author"] = ol_author
            changed["author"] = ol_author

    # summary：缺失才补（或允许覆盖）
    if ol_desc:
        if (not existing_summary) or (not FILL_MISSING_ONLY):
            s = re.sub(r"\s+", " ", ol_desc).strip()
            if len(s) > 180:
                s = s[:180].rstrip() + "…"
            if fm.get("summary") != s:
                fm["summary"] = s
                changed["summary"] = s

    # cover：建议补齐（注意：你现在用的是本地 /img/...，如果不想被覆盖就保持 FILL_MISSING_ONLY=True）
    if WRITE_COVER and cover_url:
        if (not fm.get("cover")) or (not FILL_MISSING_ONLY):
            if fm.get("cover") != cover_url:
                fm["cover"] = cover_url
                changed["cover"] = cover_url

    if changed:
        write_markdown(md_path, fm, body)
        return {"file": md_path.name, "status": "updated", "changed": changed, "work": work_key}
    else:
        return {"file": md_path.name, "status": "no_change", "work": work_key}


def main():
    if not BOOK_DIR.exists():
        raise SystemExit(f"Books dir not found: {BOOK_DIR}")

    files = sorted(BOOK_DIR.glob("*.md"))
    results = []
    for fp in files:
        try:
            res = update_one(fp)
        except Exception as e:
            res = {"file": fp.name, "status": "error", "error": str(e)}
        results.append(res)

    updated = sum(1 for r in results if r["status"] == "updated")
    not_found = sum(1 for r in results if r["status"] == "not_found")
    errors = [r for r in results if r["status"] == "error"]

    print(f"\nDONE. total={len(results)} updated={updated} not_found={not_found} error={len(errors)}\n")
    if not_found:
        print("NOT FOUND:")
        for r in results:
            if r["status"] == "not_found":
                print(" -", r["file"])
    if errors:
        print("\nERRORS:")
        for r in errors:
            print(" -", r["file"], "=>", r["error"])

    log_path = ROOT / "scripts" / "openlibrary_log.json"
    log_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nLog written: {log_path}\n")


if __name__ == "__main__":
    main()
