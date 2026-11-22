#!/usr/bin/env python3
"""Validate guide.json and embed it into index.html so the page works offline.

Usage:
    python build.py          # validate, then update index.html
    python build.py --check  # validate only; exit 1 if guide.json is invalid or index.html is out of date

Only the Python standard library is used.
"""
import datetime as dt
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
# Dates in guide.json are Taiwan dates, so "today" is computed in Taipei time (UTC+8, no daylight saving),
# whatever time zone the machine or GitHub Actions runner uses.
TAIPEI = dt.timezone(dt.timedelta(hours=8), "Asia/Taipei")
DATA = ROOT / "guide.json"
PAGE = ROOT / "index.html"
START, END = "<!-- GUIDE:START -->", "<!-- GUIDE:END -->"

GROUP_FIELDS = ["id", "title", "title_en", "order"]
CHAPTER_FIELDS = ["id", "title", "title_en", "intro", "order", "group", "status"]
CHAPTER_STATUS = {"planned", "writing", "done", "pending"}  # 規劃中 / 撰寫中 / 完成 / 待決定
ITEM_FIELDS = ["id", "chapter", "title", "cost", "plain", "benefit", "evidence", "caveats", "sources", "status", "updated"]
ITEM_STATUS = {"draft", "reviewed"}  # 草稿 / 已查核
COST_FIELDS = ["money", "time", "effort"]
SOURCE_FIELDS = ["title", "url", "type", "checked"]
EFFORTS = {"低", "中", "高"}
EVIDENCE = {"A", "B", "C"}
SOURCE_TYPES = {"官方", "法規", "期刊"}
SLUG = r"[a-z]+(-[a-z0-9]+)*"

# Hosts each source type may point to. A host matches if it equals an entry or ends with "." + entry.
# To allow a new site, add it here and explain in the pull request which agency runs it.
ALLOWED_HOSTS = {
    "官方": ["gov.tw"],
    "法規": ["law.moj.gov.tw", "laws.mol.gov.tw", "gov.tw", "laws.gov.taipei"],  # laws.gov.taipei: 臺北市政府法務局「臺北市法規查詢系統」
    "期刊": ["doi.org", "pubmed.ncbi.nlm.nih.gov", "pmc.ncbi.nlm.nih.gov", "cochranelibrary.com"],
}

# Simplified characters and mainland terms that should not appear (warning only; extend as needed).
NON_TW_TERMS = ["视频", "质量", "信息", "软件", "网络", "设置", "数据", "这", "们", "说", "为", "时", "费"]


def taipei_today():
    return dt.datetime.now(TAIPEI).date()


def nonempty_str(v):
    return isinstance(v, str) and v.strip() != ""


def parse_date(v):
    """Return a date for a real YYYY-MM-DD string, else None."""
    if not isinstance(v, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return None
    try:
        return dt.date.fromisoformat(v)
    except ValueError:
        return None


def host_allowed(host, kind):
    return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS.get(kind, []))


def check_fields(obj, fields, where, errors):
    for f in fields:
        if f not in obj:
            errors.append(f"{where}: missing field {f!r}")
    extra = set(obj) - set(fields)
    if extra:
        errors.append(f"{where}: unknown fields {sorted(extra)}")


def validate(data, today=None):
    today = today or taipei_today()
    errors, warnings = [], []
    if not isinstance(data, dict):
        return ["guide.json must be an object with groups, chapters and items"], []
    check_fields(data, ["groups", "chapters", "items"], "guide.json", errors)
    groups = data.get("groups") if isinstance(data.get("groups"), list) else []
    chapters = data.get("chapters") if isinstance(data.get("chapters"), list) else []
    items = data.get("items") if isinstance(data.get("items"), list) else []
    if not groups:
        errors.append("groups must be a non-empty list")
    if not chapters:
        errors.append("chapters must be a non-empty list")

    # Groups
    group_ids = set()
    for i, g in enumerate(groups):
        where = f"groups[{i}]"
        if not isinstance(g, dict):
            errors.append(f"{where}: must be an object")
            continue
        check_fields(g, GROUP_FIELDS, where, errors)
        for f in ("id", "title", "title_en"):
            if not nonempty_str(g.get(f)):
                errors.append(f"{where}: {f} must be a non-empty string")
        if not isinstance(g.get("order"), int):
            errors.append(f"{where}: order must be a whole number")
        gid = g.get("id")
        if gid in group_ids:
            errors.append(f"{where}: duplicate group id {gid!r}")
        group_ids.add(gid)

    # Chapters
    chapter_ids = set()
    for i, c in enumerate(chapters):
        where = f"chapters[{i}] ({c.get('id', '?') if isinstance(c, dict) else '?'})"
        if not isinstance(c, dict):
            errors.append(f"{where}: must be an object")
            continue
        check_fields(c, CHAPTER_FIELDS, where, errors)
        for f in ("id", "title", "title_en", "intro"):
            if not nonempty_str(c.get(f)):
                errors.append(f"{where}: {f} must be a non-empty string")
        if not re.fullmatch(SLUG, str(c.get("id", ""))):
            errors.append(f"{where}: id must be lowercase letters, digits and dashes")
        if c.get("id") in chapter_ids:
            errors.append(f"{where}: duplicate chapter id")
        chapter_ids.add(c.get("id"))
        if not isinstance(c.get("order"), int):
            errors.append(f"{where}: order must be a whole number")
        if c.get("group") not in group_ids:
            errors.append(f"{where}: group {c.get('group')!r} is not listed in groups")
        if c.get("status") not in CHAPTER_STATUS:
            errors.append(f"{where}: status must be one of {sorted(CHAPTER_STATUS)}")

    # Items
    seen = set()
    per_chapter = {}
    for i, it in enumerate(items):
        where = f"items[{i}] ({it.get('id', '?') if isinstance(it, dict) else '?'})"
        if not isinstance(it, dict):
            errors.append(f"{where}: must be an object")
            continue
        check_fields(it, ITEM_FIELDS, where, errors)
        for f in ("id", "chapter", "title", "plain", "benefit", "caveats"):
            if f in it and not nonempty_str(it[f]):
                errors.append(f"{where}: {f} must be a non-empty string")

        iid, chap = str(it.get("id", "")), str(it.get("chapter", ""))
        if chap not in chapter_ids:
            errors.append(f"{where}: chapter {chap!r} is not listed in chapters")
        per_chapter[chap] = per_chapter.get(chap, 0) + 1
        if not re.fullmatch(re.escape(chap) + r"-\d{3}", iid):
            errors.append(f"{where}: id must be <chapter>-<3 digits>, e.g. {chap}-001")
        if iid in seen:
            errors.append(f"{where}: duplicate id")
        seen.add(iid)

        cost = it.get("cost")
        cost_d = cost if isinstance(cost, dict) else {}
        if not isinstance(cost, dict):
            errors.append(f"{where}: cost must be an object with money, time, effort")
        else:
            check_fields(cost, COST_FIELDS, f"{where} cost", errors)
            for f in COST_FIELDS:
                if not nonempty_str(cost.get(f)):
                    errors.append(f"{where}: cost.{f} must be a non-empty string")
            if cost.get("effort") not in EFFORTS:
                errors.append(f"{where}: cost.effort must be one of 低/中/高")

        if it.get("evidence") not in EVIDENCE:
            errors.append(f"{where}: evidence must be A, B or C")
        if it.get("status") not in ITEM_STATUS:
            errors.append(f"{where}: status must be draft or reviewed")
        upd = parse_date(it.get("updated"))
        if upd is None:
            errors.append(f"{where}: updated must be a real date written YYYY-MM-DD")
        elif upd > today:
            errors.append(f"{where}: updated date {it.get('updated')} is in the future")

        sources = it.get("sources")
        if not isinstance(sources, list) or not sources:
            errors.append(f"{where}: sources must be a non-empty list")
            sources = []
        for j, s in enumerate(sources):
            sw = f"{where} sources[{j}]"
            if not isinstance(s, dict):
                errors.append(f"{sw}: must be an object")
                continue
            check_fields(s, SOURCE_FIELDS, sw, errors)
            for f in SOURCE_FIELDS:
                if not nonempty_str(s.get(f)):
                    errors.append(f"{sw}: {f} must be a non-empty string")
            kind = s.get("type")
            if kind not in SOURCE_TYPES:
                errors.append(f"{sw}: type must be 官方, 法規 or 期刊")
            url = s.get("url", "")
            p = urlparse(url) if isinstance(url, str) else None
            if not p or p.scheme != "https" or not p.hostname or re.search(r"\s", url):
                errors.append(f"{sw}: url must be a full https:// address")
            elif kind in SOURCE_TYPES and not host_allowed(p.hostname.lower(), kind):
                errors.append(f"{sw}: host {p.hostname} is not on the allowed list for {kind} (see ALLOWED_HOSTS in build.py)")
            d = parse_date(s.get("checked"))
            if d is None:
                errors.append(f"{sw}: checked must be a real date written YYYY-MM-DD")
            elif d > today:
                errors.append(f"{sw}: checked date {s.get('checked')} is in the future")
            elif (today - d).days > 365:
                warnings.append(f"{sw}: checked {s.get('checked')} is more than a year old; please re-check")

        # Warnings that need a human to judge.
        text = " ".join(str(it.get(f, "")) for f in ("title", "plain", "benefit", "caveats"))
        text += " " + " ".join(str(v) for v in cost_d.values())
        for term in NON_TW_TERMS:
            if term in text:
                warnings.append(f"{where}: contains {term!r}; please use Taiwan Traditional Chinese")
        numbers_text = str(it.get("benefit", "")) + " " + str(cost_d.get("money", ""))
        if re.search(r"\d", numbers_text) and not re.search(r"(19|20)\d{2}|民國\s*\d{2,3}", numbers_text):
            warnings.append(f"{where}: benefit/cost has numbers but no year; say which year they apply to")
        if it.get("evidence") == "C" and not re.search(r"爭議|證據有限", str(it.get("caveats", ""))):
            warnings.append(f"{where}: evidence C should explain the controversy or limits in caveats")
        if not re.search(r"醫師|諮詢|專業人士", str(it.get("caveats", ""))):
            warnings.append(f"{where}: caveats should remind readers to ask a doctor or a professional")

    for c in chapters:
        if isinstance(c, dict) and c.get("status") == "planned" and per_chapter.get(c.get("id")):
            warnings.append(f"chapter {c.get('id')}: has items but status is still planned")
    return errors, warnings


def render(data, page):
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    block = f'{START}\n<script id="guide-data" type="application/json">{payload}</script>\n{END}'
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
    if not pattern.search(page):
        sys.exit("index.html is missing the GUIDE:START / GUIDE:END markers")
    return pattern.sub(lambda _: block, page)


def main():
    try:
        data = json.loads(DATA.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"guide.json is not valid JSON: {e}")
    errors, warnings = validate(data)
    for w in warnings:
        print("warning:", w)
    if errors:
        print("guide.json has problems:\n  " + "\n  ".join(errors))
        sys.exit(1)
    page = PAGE.read_text(encoding="utf-8")
    new = render(data, page)
    summary = f"{len(data['chapters'])} chapters, {len(data['items'])} items"
    if "--check" in sys.argv:
        if new != page:
            print("index.html is out of date. Run: python build.py")
            sys.exit(1)
        print(f"OK: {summary}, index.html is up to date")
        return
    PAGE.write_text(new, encoding="utf-8")
    print(f"Embedded {summary} into index.html")


if __name__ == "__main__":
    main()
