# scripts/generate/_books_openlibrary_fill.py
import re
import time
import json
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List

import requests
import yaml

# ========= 路径：自动识别项目根（兼容 scripts/ 与 scripts/generate/）=========
here = Path(__file__).resolve()

def is_root(p: Path) -> bool:
    return (p / "content").exists() and (p / "layouts").exists() and (p / "hugo.toml").exists()

# here = ...\scripts\generate\_books_openlibrary_fill.py
cand = [here.parents[2], here.parents[1], here.parents[0]]  # 优先更上层
ROOT = None
for p in cand:
    if is_root(p):
        ROOT = p
        break
if ROOT is None:
    # 兜底：向上最多回溯 5 层
    p = here
    for _ in range(6):
        if is_root(p):
            ROOT = p
            break
        p = p.parent
if ROOT is None:
    raise SystemExit(f"Cannot locate project ROOT from: {here}")

BOOK_DIR = ROOT / "content" / "books"

SEARCH_API = "https://openlibrary.org/search.json"
WORK_API_PREFIX = "https://openlibrary.org"          # + /works/OLxxxxW.json
COVER_PREFIX = "https://covers.openlibrary.org/b/id" # /{id}-L.jpg
ISBN_API_PREFIX = "https://openlibrary.org/isbn"     # /{isbn}.json

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "wuwu-books/1.0 (OpenLibrary lookup; contact: none)",
    "Accept": "application/json,text/plain,*/*",
})

# 你可以调的策略
FILL_MISSING_ONLY = True   # True: 只补缺失字段；False: 覆盖写入
WRITE_COVER = True         # True: 写入 cover（建议开启）
SLEEP_SEC = 0.35           # 间隔稍微大一点更稳
TIMEOUT_SEC = 25


# ========= front matter 读写 =========
def read_front_matter(md_text: str) -> Tuple[Dict[str, Any], str]:
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


# ========= OpenLibrary 请求 =========
def _get_json(url: str, params: Optional[dict] = None) -> dict:
    r = SESSION.get(url, params=params, timeout=TIMEOUT_SEC)
    # 422/400 这类，直接抛出去给上层做 fallback
    r.raise_for_status()
    return r.json()

def ol_search_by_q(q: str, limit: int = 10) -> Optional[Dict[str, Any]]:
    data = _get_json(SEARCH_API, params={"q": q, "limit": limit})
    docs = data.get("docs") or []
    if not docs:
        return None

    def score(d: Dict[str, Any]) -> int:
        s = 0
        if d.get("key"): s += 3
        if d.get("cover_i"): s += 2
        if d.get("author_name"): s += 2
        if d.get("first_publish_year"): s += 1
        if d.get("edition_count"): s += 1
        return s

    docs.sort(key=score, reverse=True)
    return docs[0]

def ol_work_detail(work_key: str) -> Optional[Dict[str, Any]]:
    url = f"{WORK_API_PREFIX}{work_key}.json"
    r = SESSION.get(url, timeout=TIMEOUT_SEC)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()

def ol_isbn_lookup(isbn: str) -> Optional[Dict[str, Any]]:
    url = f"{ISBN_API_PREFIX}/{isbn}.json"
    r = SESSION.get(url, timeout=TIMEOUT_SEC)
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

def pick_author(search_doc: Dict[str, Any]) -> Optional[str]:
    names = search_doc.get("author_name")
    if isinstance(names, list) and names:
        return str(names[0]).strip()
    return None

def extract_cover_id(search_doc: Dict[str, Any], work: Optional[Dict[str, Any]]) -> Optional[int]:
    if search_doc and search_doc.get("cover_i"):
        try:
            return int(search_doc["cover_i"])
        except Exception:
            pass
    if work and isinstance(work.get("covers"), list) and work["covers"]:
        try:
            return int(work["covers"][0])
        except Exception:
            return None
    return None

def normalize_title_variants(title: str) -> List[str]:
    t = (title or "").strip()
    if not t:
        return []
    variants = [t]

    # 去掉副标题（英文常见）
    if ":" in t:
        variants.append(t.split(":", 1)[0].strip())

    # 去掉中文冒号副标题
    if "：" in t:
        variants.append(t.split("：", 1)[0].strip())

    # 去掉多余空白
    variants.append(re.sub(r"\s+", " ", t).strip())

    # 去掉引号/特殊符号（有时影响检索）
    variants.append(re.sub(r"[\"'“”‘’]", "", t).strip())

    # 去重保持顺序
    out = []
    seen = set()
    for x in variants:
        if x and x not in seen:
            out.append(x)
            seen.add(x)
    return out


# ========= 单本更新 =========
def update_one(md_path: Path) -> Dict[str, Any]:
    raw = md_path.read_text(encoding="utf-8")
    fm, body = read_front_matter(raw)

    title = (fm.get("title") or "").strip()
    author = (fm.get("author") or "").strip() or None
    summary = (fm.get("summary") or "").strip()

    # 允许你手动指定 OpenLibrary 搜索用的字段
    ol_title = (fm.get("ol_title") or "").strip() or None
    ol_author = (fm.get("ol_author") or "").strip() or None
    ol_work = (fm.get("ol_work") or "").strip() or None   # 例如 /works/OL18147675W
    ol_isbn = (fm.get("ol_isbn") or "").strip() or None   # 例如 978xxxx

    if not title:
        return {"file": md_path.name, "status": "skip", "reason": "missing title"}

    # 1) 如果你给了 work key：直接命中（最稳）
    work = None
    work_key = None
    search_doc = None

    if ol_work:
        work_key = ol_work if ol_work.startswith("/works/") else f"/works/{ol_work}"
        work = ol_work_detail(work_key)
        time.sleep(SLEEP_SEC)

    # 2) 如果给了 ISBN：走 isbn -> works（次稳）
    if not work and ol_isbn:
        ed = ol_isbn_lookup(ol_isbn)
        time.sleep(SLEEP_SEC)
        if ed and isinstance(ed.get("works"), list) and ed["works"]:
            wk = ed["works"][0].get("key")
            if wk:
                work_key = wk
                work = ol_work_detail(work_key)
                time.sleep(SLEEP_SEC)

    # 3) 常规搜索：多策略 q=
    if not work:
        t_candidates = normalize_title_variants(ol_title or title)
        a = ol_author or author

        queries = []
        # title+author
        if a:
            for t in t_candidates:
                queries.append(f'title:"{t}" author:"{a}"')
        # 只 title
        for t in t_candidates:
            queries.append(f'title:"{t}"')

        # 最后兜底：直接 q=原字符串（中文更容易命中）
        if ol_title:
            queries.append(ol_title)
        else:
            queries.append(title)

        last_err = None
        for q in queries:
            try:
                search_doc = ol_search_by_q(q=q, limit=10)
                time.sleep(SLEEP_SEC)
            except Exception as e:
                last_err = str(e)
                continue

            if search_doc and search_doc.get("key"):
                work_key = search_doc["key"]
                try:
                    work = ol_work_detail(work_key)
                except Exception as e:
                    last_err = str(e)
                    work = None
                time.sleep(SLEEP_SEC)
                if work:
                    break

        if not work:
            return {
                "file": md_path.name,
                "status": "not_found",
                "title_used": (ol_title or title),
                "author_used": (ol_author or author),
                "error": last_err,
            }

    # 提取字段
    ol_desc = extract_description(work) if work else None
    found_author = pick_author(search_doc) if search_doc else None
    cover_id = extract_cover_id(search_doc, work)
    cover_url = f"{COVER_PREFIX}/{cover_id}-L.jpg" if cover_id else None

    changed = {}

    # author：缺失才补（或允许覆盖）
    if found_author and ((not author) or (not FILL_MISSING_ONLY)):
        if fm.get("author") != found_author:
            fm["author"] = found_author
            changed["author"] = found_author

    # summary：缺失才补（或允许覆盖）
    if ol_desc:
        if (not summary) or (not FILL_MISSING_ONLY):
            s = re.sub(r"\s+", " ", ol_desc).strip()
            if len(s) > 180:
                s = s[:180].rstrip() + "…"
            if fm.get("summary") != s:
                fm["summary"] = s
                changed["summary"] = s

    # cover：建议补齐
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
    not_found = [r for r in results if r["status"] == "not_found"]
    errors = [r for r in results if r["status"] == "error"]

    print(f"\nDONE. root={ROOT}")
    print(f"total={len(results)} updated={updated} not_found={len(not_found)} error={len(errors)}\n")

    if not_found:
        print("NOT FOUND:")
        for r in not_found:
            extra = ""
            if r.get("title_used"):
                extra = f"  (title_used={r.get('title_used')!r}, author_used={r.get('author_used')!r})"
            print(" -", r["file"], extra)

    if errors:
        print("\nERRORS:")
        for r in errors:
            print(" -", r["file"], "=>", r["error"])

    log_path = ROOT / "scripts" / "openlibrary_log.json"
    log_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nLog written: {log_path}\n")


if __name__ == "__main__":
    main()
