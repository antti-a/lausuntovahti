#!/usr/bin/env python3
"""
Lausuntovahti – merge, score, and build the ICS calendar.

Usage:
  python3 scripts/build_calendar.py [--incoming incoming/2026-11-02.json] [--today 2026-11-02]

Inputs
  config/keywords.json   scoring rules (editable)
  config/kunnat.json     municipalities watched
  state/items.json       all items seen so far (the persistent state)
  incoming/<date>.json   items extracted in this run (optional)

Outputs
  state/items.json       updated (merged, rescored)
  state/archive.json     items whose deadline passed > ARCHIVE_AFTER_DAYS ago
  calendar.ics           high + medium priority items: an event on the deadline (or on the run
                         day while the deadline is unknown) and a "UUSI" event on the day found
  docs/summary-<date>.md run summary for the weekly message
  docs/all-items.md      human-readable table of everything currently tracked

Item schema (incoming and state):
  {
    "source_id": "evk_uusimaa",            # must match config/sources.json
    "title": "...",                         # as published, short
    "url": "https://...",                   # direct link to the kuulutus / documents
    "kunta": ["Vantaa", "Tuusula"],         # [] if none / regional
    "type": "YVA",                          # free text; see TASK_PROMPT.md for vocabulary
    "action": "mielipide",                  # mielipide | muistutus | lausunto | vastine | tiedoksi | päätös
    "published": "2026-10-21",              # YYYY-MM-DD (or d.m.yyyy) or null
    "deadline": "2026-11-20",               # YYYY-MM-DD (or d.m.yyyy) or null
    "deadline_confidence": "exact",         # exact | estimated | unknown
    "summary": "1–2 sentences: what it is, why it matters for water."
  }
Optional fields the script preserves: "manual_priority" (high|medium|low|exclude), "notes".
Script-managed fields: id, score, priority, first_seen, last_seen, reasons.

An incoming item may carry the "id" of a state item to update it (better link or deadline) even
when its title is worded differently. An incoming item with the same direct url as a known item
updates that item too. Any other date format stops the run before anything is written.
"""

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
STATE = ROOT / "state"
DOCS = ROOT / "docs"
ICS_PATH = ROOT / "calendar.ics"

ARCHIVE_AFTER_DAYS = 60     # move items to archive this long after the deadline
ICS_PAST_DAYS = 30          # keep events in ICS this long after the deadline
CAL_NAME = "Lausuntovahti – Vantaanjoki, Espoonjoki, Mankinjoki"


# ---------------------------------------------------------------- helpers

def load_json(path, default):
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def norm(s):
    """lowercase, strip accents-insensitive? No: Finnish ä/ö matter. Just casefold + collapse spaces."""
    s = unicodedata.normalize("NFC", s or "")
    return re.sub(r"\s+", " ", s.casefold()).strip()


def item_id(item):
    key = f"{item['source_id']}|{norm(item['title'])}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def parse_date(s):
    if not s:
        return None
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        return None


FI_DATE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")


def clean_date(s):
    """YYYY-MM-DD or d.m.yyyy → YYYY-MM-DD; empty → None; anything else raises ValueError."""
    if not s:
        return None
    s = str(s).strip()
    m = FI_DATE.fullmatch(s)
    if m:
        d, mo, y = map(int, m.groups())
        return dt.date(y, mo, d).isoformat()
    return dt.date.fromisoformat(s).isoformat()


MAX_SUFFIX = 5  # allowed inflection tail after a keyword (vesistö→vesistöjen); longer tails are compounds


def keyword_regex(k):
    """'sana' = start of a word + at most MAX_SUFFIX more letters.
    'sana*' = start of a word + anything (Vantaanjo* → Vantaanjokeen).
    '*sana' = may also be the end of a compound word (*joki → Mätäjoki)."""
    kn = norm(k)
    left = "" if kn.startswith("*") else r"(?<![\wäöå])"
    right = "" if kn.endswith("*") else r"[\wäöå]{0," + str(MAX_SUFFIX) + r"}(?![\wäöå])"
    return left + re.escape(kn.strip("*")) + right


def contains_any(text, keywords):
    """Keyword must start a word, and the rest of that word must be a short inflection,
    so 'järvi' does not hit 'Nurmijärvi' and 'suo' does not hit 'suojelualue'. '*' loosens this."""
    return [k for k in keywords if re.search(keyword_regex(k), text)]


# ---------------------------------------------------------------- scoring

def score_item(item, rules, kunnat):
    """Return (score, priority, reasons)."""
    head = norm(f"{item.get('title','')} {item.get('type','')}")
    text = f"{head} {norm(item.get('summary',''))}"
    reasons = []

    # manual override
    mp = item.get("manual_priority")
    if mp == "exclude":
        return -99, "exclude", ["manual_priority=exclude"]

    excl = contains_any(text, rules["exclude_keywords"])
    if excl and not mp:
        return -99, "exclude", [f"exclude_keyword: {excl[0]}"]

    sc = rules["scoring"]
    score = 0

    # the summary is written to discuss water, so a hit there alone is weak evidence
    water = contains_any(head, rules["water_keywords"])
    if water:
        score += sc["water_hit"]
        reasons.append(f"water: {', '.join(water[:3])}")
    else:
        water = contains_any(text, rules["water_keywords"])
        if water:
            score += sc.get("water_hit_summary_only", 1)
            reasons.append(f"water (summary only): {', '.join(water[:3])}")

    plan = contains_any(text, rules["large_plan_keywords"])
    if plan:
        score += sc["large_plan_hit"]
        reasons.append(f"plan: {', '.join(plan[:3])}")

    action = norm(item.get("action", ""))
    aw = rules["action_weights"].get(action, 0)
    if aw:
        score += aw
        reasons.append(f"action {action}: {aw:+d}")

    regional_ok = norm(item.get("type", "")) in {norm(t) for t in sc["regional_types_without_kunta"]}

    # kunta match: either listed, or a watched kunta name appears in the text
    kunta_hit = bool(item.get("kunta")) and any(
        norm(k) in {norm(x["name"]) for x in kunnat} for k in item["kunta"]
    )
    if not kunta_hit and not regional_ok:
        for k in kunnat:
            names = [k["name"]] + k.get("aliases", [])
            if any(norm(n) in text for n in names):
                kunta_hit = True
                item.setdefault("kunta", [])
                if k["name"] not in item["kunta"]:
                    item["kunta"].append(k["name"])
                break
    if kunta_hit:
        score += sc["kunta_match"]
        reasons.append("kunta match")

    th = rules["thresholds"]
    cap = rules.get("non_response_actions_max_priority", {})
    if mp in ("high", "medium", "low"):
        priority = mp
        reasons.append(f"manual_priority={mp}")
    elif action in cap:
        priority = cap[action]
        reasons.append(f"action {action} → max {priority}")
    elif not kunta_hit and not regional_ok:
        priority = "low"
        reasons.append("no kunta match → low")
    elif score >= th["high"]:
        priority = "high"
    elif score >= th["medium"]:
        priority = "medium"
    else:
        priority = "low"
    return score, priority, reasons


# ---------------------------------------------------------------- merge

CONFIDENCE_RANK = {"unknown": 0, "estimated": 1, "exact": 2}


def confidence(item):
    return CONFIDENCE_RANK.get(item.get("deadline_confidence"), 0)


def specific_url(url, listing_urls):
    """The url if it points at one notice; None for empty urls, site roots and the sources' listing pages."""
    u = (url or "").strip().rstrip("/")
    if not u or urlparse(u).path in ("", "/") or u in listing_urls:
        return None
    return u


def merge(state_items, incoming, today, archived_ids=frozenset(), listing_urls=frozenset()):
    by_id = {it["id"]: it for it in state_items}
    by_url = {u: it for it in state_items if (u := specific_url(it.get("url"), listing_urls))}
    new, updated = [], []
    for raw in incoming:
        if not raw.get("title") or not raw.get("source_id"):
            continue
        raw.setdefault("kunta", [])
        raw.setdefault("deadline_confidence", "exact" if raw.get("deadline") else "unknown")
        known_id = raw.pop("id", None)  # the extractor points at a known item worded differently
        iid = item_id(raw)
        if iid in archived_ids or known_id in archived_ids:
            continue  # already handled and expired
        url = specific_url(raw.get("url"), listing_urls)
        cur = by_id.get(known_id) or by_id.get(iid) or by_url.get(url)
        if cur:
            changed = False
            # a deadline at least as certain as the current one wins (corrections, refined estimates)
            if raw.get("deadline") and confidence(raw) >= confidence(cur) and \
                    (raw["deadline"], raw["deadline_confidence"]) != (cur.get("deadline"), cur.get("deadline_confidence")):
                cur["deadline"] = raw["deadline"]
                cur["deadline_confidence"] = raw["deadline_confidence"]
                changed = True
            # a direct link replaces a listing page
            if url and not specific_url(cur.get("url"), listing_urls):
                cur["url"] = raw["url"]
                by_url[url] = cur
                changed = True
            for f in ("url", "summary", "published", "type", "action"):
                if raw.get(f) and not cur.get(f):
                    cur[f] = raw[f]
                    changed = True
            for k in raw.get("kunta", []):
                if k not in cur["kunta"]:
                    cur["kunta"].append(k)
                    changed = True
            cur["last_seen"] = today.isoformat()
            if changed and cur["id"] not in {x["id"] for x in new + updated}:
                updated.append(cur)
        else:
            raw["id"] = iid
            raw["first_seen"] = today.isoformat()
            raw["last_seen"] = today.isoformat()
            by_id[iid] = raw
            if url:
                by_url[url] = raw
            new.append(raw)
    return list(by_id.values()), new, updated


# ---------------------------------------------------------------- ICS

def ics_escape(s):
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(line):
    """RFC 5545 line folding at 75 octets."""
    out, enc = [], line.encode("utf-8")
    while len(enc) > 75:
        cut = 75
        while cut > 0 and (enc[cut] & 0xC0) == 0x80:  # don't split a UTF-8 sequence
            cut -= 1
        out.append(enc[:cut].decode("utf-8"))
        enc = b" " + enc[cut:]
    out.append(enc.decode("utf-8"))
    return "\r\n".join(out)


def fi_date(d):
    return f"{d.day}.{d.month}.{d.year}"


def vevent(uid, day, summary, desc, it, stamp):
    return [
        "BEGIN:VEVENT",
        f"UID:{uid}@lausuntovahti",
        f"DTSTAMP:{stamp}",
        f"DTSTART;VALUE=DATE:{day.strftime('%Y%m%d')}",
        f"DTEND;VALUE=DATE:{(day + dt.timedelta(days=1)).strftime('%Y%m%d')}",
        f"SUMMARY:{ics_escape(summary)}",
        f"DESCRIPTION:{ics_escape(desc)}",
        f"URL:{it.get('url','')}",
        f"CATEGORIES:{ics_escape(it['priority'])},{ics_escape(it.get('type',''))}",
        "TRANSP:TRANSPARENT",
        "END:VEVENT",
    ]


def build_ics(items, today, stamp):
    """Each high/medium item gets an event on its deadline, or on the run day while the deadline is
    unknown (so it moves forward every week), plus a "UUSI" event on the day it was first seen."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Lausuntovahti//FI",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ics_escape(CAL_NAME)}",
        "X-WR-TIMEZONE:Europe/Helsinki",
        "X-PUBLISHED-TTL:PT24H",
    ]
    cutoff = today - dt.timedelta(days=ICS_PAST_DAYS)
    events = []
    for it in items:
        if it["priority"] not in ("high", "medium"):
            continue
        d = parse_date(it.get("deadline"))
        found = parse_date(it.get("first_seen"))
        prio_tag = "!!" if it["priority"] == "high" else "!"
        est = " (arvio)" if it.get("deadline_confidence") == "estimated" else ""
        kunta = ", ".join(it.get("kunta") or []) or "alueellinen"
        action = it.get("action") or "lausunto"
        desc = "\n".join(([it["summary"], ""] if it.get("summary") else []) + [
            f"Tyyppi: {it.get('type','')} | Toimenpide: {action} | Prioriteetti: {it['priority']} (pisteet {it['score']})",
            f"Kunta: {kunta}",
            f"Julkaistu: {it.get('published') or '?'} | Määräpäivä: {fi_date(d) + est if d else 'ei tiedossa'}",
            f"Lähde: {it['source_id']}",
            f"Linkki: {it.get('url','')}",
            f"Perustelut: {'; '.join(it.get('reasons', []))}",
        ] + ([] if d else ["", "Määräpäivä ei ole vielä tiedossa – tarkista linkistä. Tapahtuma siirtyy "
                               "joka viikko ajopäivälle, kunnes määräpäivä selviää."]))
        if not d:
            events.append((today, vevent(it["id"], today,
                                         f"{prio_tag} {action.upper()} DL ?: {it['title']} [{kunta}]", desc, it, stamp)))
            continue
        if d >= cutoff:
            events.append((d, vevent(it["id"], d,
                                     f"{prio_tag} {action.upper()} DL{est}: {it['title']} [{kunta}]", desc, it, stamp)))
        if found and cutoff <= found < d:
            events.append((found, vevent(f"{it['id']}-uusi", found,
                                         f"{prio_tag} UUSI, {action.upper()} DL {fi_date(d)}{est}: {it['title']} [{kunta}]",
                                         desc, it, stamp)))
    for _, ev in sorted(events, key=lambda e: e[0]):
        lines += ev
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n", len(events)


# ---------------------------------------------------------------- docs

def md_table(items):
    rows = ["| Prio | Määräpäivä | Toimenpide | Otsikko | Kunta | Tyyppi | Lähde |", "|---|---|---|---|---|---|---|"]
    for it in sorted(items, key=lambda x: (x.get("deadline") or "9999", -x["score"])):
        est = " (arvio)" if it.get("deadline_confidence") == "estimated" else ""
        rows.append(
            f"| {it['priority']} | {it.get('deadline') or '?'}{est} | {it.get('action','')} | "
            f"[{it['title']}]({it.get('url','')}) | {', '.join(it.get('kunta') or []) or '–'} | "
            f"{it.get('type','')} | {it['source_id']} |"
        )
    return "\n".join(rows)


def write_summary(path, today, new, updated, items, archived, failed_sources, ics_count):
    def sec(title, lst, empty="_ei uusia_"):
        if not lst:
            return f"### {title}\n{empty}\n"
        return f"### {title}\n" + md_table(lst) + "\n"
    new_high = [i for i in new if i["priority"] == "high"]
    new_med = [i for i in new if i["priority"] == "medium"]
    new_low = [i for i in new if i["priority"] == "low"]
    new_excl = [i for i in new if i["priority"] == "exclude"]
    upcoming = [i for i in items if i["priority"] in ("high", "medium") and parse_date(i.get("deadline"))
                and today <= parse_date(i["deadline"]) <= today + dt.timedelta(days=14)]
    nodl = [i for i in new if i["priority"] in ("high", "medium") and not i.get("deadline")]
    txt = f"""# Lausuntovahti – ajo {today.isoformat()}

Kalenterissa nyt {ics_count} tapahtumaa. Uusia: {len(new)} (high {len(new_high)}, medium {len(new_med)}, low {len(new_low)}, poissuljettu {len(new_excl)}). Päivitettyjä: {len(updated)}. Arkistoitu: {len(archived)}.

{sec("Uudet – HIGH (kalenteriin)", new_high)}
{sec("Uudet – MEDIUM (kalenteriin)", new_med)}
{sec("Määräpäivä seuraavan 14 päivän sisällä", upcoming, "_ei kohteita_")}
{sec("Uudet high/medium ILMAN määräpäivää – tarkista käsin", nodl)}
{sec("Uudet – LOW (vain tämä yhteenveto)", new_low)}
### Lähteet joiden luku epäonnistui
{chr(10).join('- ' + s for s in failed_sources) if failed_sources else '_ei ongelmia_'}
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(txt, encoding="utf-8")
    return txt


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--incoming", help="JSON file with items extracted this run")
    ap.add_argument("--today", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--failed", nargs="*", default=[], help="source ids that could not be read this run")
    args = ap.parse_args()

    today = parse_date(args.today) or dt.date.today()
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    rules = load_json(CONFIG / "keywords.json", None)
    kunnat = load_json(CONFIG / "kunnat.json", {"kunnat": []})["kunnat"]
    sources = load_json(CONFIG / "sources.json", {"sources": []})["sources"]
    if not rules:
        sys.exit("config/keywords.json missing")

    state_items = load_json(STATE / "items.json", [])
    archive = load_json(STATE / "archive.json", [])
    incoming = []
    if args.incoming:
        inc = load_json(Path(args.incoming), [])
        incoming = inc["items"] if isinstance(inc, dict) else inc

    # a date the script can't read would silently keep the item out of the calendar: stop instead
    errors = []
    for raw in incoming:
        for f in ("published", "deadline"):
            try:
                raw[f] = clean_date(raw.get(f))
            except ValueError:
                errors.append(f"  {raw.get('title', '?')}: {f} = {raw.get(f)!r}")
    if errors:
        sys.exit("Virheellinen päivämäärä (käytä YYYY-MM-DD), korjaa incoming-tiedosto:\n" + "\n".join(errors))

    listing_urls = frozenset(s["url"].strip().rstrip("/") for s in sources if s.get("url"))
    items, new, updated = merge(state_items, incoming, today,
                                archived_ids=frozenset(a["id"] for a in archive), listing_urls=listing_urls)

    # rescore everything (rules may have changed)
    for it in items:
        it["score"], it["priority"], it["reasons"] = score_item(it, rules, kunnat)

    # archive old
    keep, archived = [], []
    for it in items:
        d = parse_date(it.get("deadline"))
        if d and d < today - dt.timedelta(days=ARCHIVE_AFTER_DAYS):
            archived.append(it)
        elif not d and parse_date(it.get("first_seen")) and \
                parse_date(it["first_seen"]) < today - dt.timedelta(days=120):
            archived.append(it)  # unknown deadline, seen long ago
        else:
            keep.append(it)
    items = keep
    archive += archived

    ics, ics_count = build_ics(items, today, stamp)
    ICS_PATH.write_bytes(ics.encode("utf-8"))  # bytes: text mode on Windows would turn CRLF into CRCRLF
    save_json(STATE / "items.json", sorted(items, key=lambda x: x.get("deadline") or "9999"))
    save_json(STATE / "archive.json", archive)

    (DOCS / "all-items.md").write_text(
        f"# Kaikki seurannassa olevat kohteet ({today.isoformat()})\n\n" + md_table(items) + "\n", encoding="utf-8")
    summary = write_summary(DOCS / f"summary-{today.isoformat()}.md", today, new, updated, items,
                            archived, args.failed, ics_count)
    print(summary)


if __name__ == "__main__":
    main()
