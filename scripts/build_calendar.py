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
  calendar.ics           high + medium priority items with a deadline
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
    "published": "2026-10-21",              # YYYY-MM-DD or null
    "deadline": "2026-11-20",               # YYYY-MM-DD or null
    "deadline_confidence": "exact",         # exact | estimated | unknown
    "summary": "1–2 sentences: what it is, why it matters for water."
  }
Optional fields the script preserves: "manual_priority" (high|medium|low|exclude), "notes".
Script-managed fields: id, score, priority, first_seen, last_seen, reasons.
"""

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

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


MAX_SUFFIX = 5  # allowed inflection tail after a keyword (joki→joessa, vesistö→vesistöjen); longer tails are compounds


def contains_any(text, keywords):
    """Keyword must start a word, and the rest of that word must be a short inflection,
    so 'järvi' does not hit 'Nurmijärvi' and 'suo' does not hit 'suojelualue'."""
    hits = []
    for k in keywords:
        kn = norm(k)
        pat = r"(?<![\wäöå])" + re.escape(kn) + r"([\wäöå]{0," + str(MAX_SUFFIX) + r"})(?![\wäöå])"
        if re.search(pat, text):
            hits.append(k)
    return hits


# ---------------------------------------------------------------- scoring

def score_item(item, rules, kunnat):
    """Return (score, priority, reasons)."""
    text = norm(f"{item.get('title','')} {item.get('summary','')} {item.get('type','')}")
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

    water = contains_any(text, rules["water_keywords"])
    if water:
        score += sc["water_hit"]
        reasons.append(f"water: {', '.join(water[:3])}")

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

def merge(state_items, incoming, today, archived_ids=frozenset()):
    by_id = {it["id"]: it for it in state_items}
    new, updated = [], []
    for raw in incoming:
        if not raw.get("title") or not raw.get("source_id"):
            continue
        raw.setdefault("kunta", [])
        raw.setdefault("deadline_confidence", "exact" if raw.get("deadline") else "unknown")
        iid = item_id(raw)
        if iid in archived_ids:
            continue  # already handled and expired
        if iid in by_id:
            cur = by_id[iid]
            changed = False
            # better deadline wins
            if raw.get("deadline") and (
                not cur.get("deadline")
                or (cur.get("deadline_confidence") != "exact" and raw.get("deadline_confidence") == "exact")
            ):
                cur["deadline"] = raw["deadline"]
                cur["deadline_confidence"] = raw.get("deadline_confidence", "exact")
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
            if changed:
                updated.append(cur)
        else:
            raw["id"] = iid
            raw["first_seen"] = today.isoformat()
            raw["last_seen"] = today.isoformat()
            by_id[iid] = raw
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


def build_ics(items, today, stamp):
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
    count = 0
    for it in sorted(items, key=lambda x: x.get("deadline") or "9999"):
        if it["priority"] not in ("high", "medium"):
            continue
        d = parse_date(it.get("deadline"))
        if not d or d < cutoff:
            continue
        prio_tag = "!!" if it["priority"] == "high" else "!"
        est = " (arvio)" if it.get("deadline_confidence") == "estimated" else ""
        kunta = ", ".join(it.get("kunta") or []) or "alueellinen"
        action = it.get("action") or "lausunto"
        summary = f"{prio_tag} {action.upper()} DL{est}: {it['title']} [{kunta}]"
        desc = "\n".join(filter(None, [
            it.get("summary", ""),
            "",
            f"Tyyppi: {it.get('type','')} | Toimenpide: {action} | Prioriteetti: {it['priority']} (pisteet {it['score']})",
            f"Kunta: {kunta}",
            f"Julkaistu: {it.get('published') or '?'} | Määräpäivä: {it.get('deadline')}{est}",
            f"Lähde: {it['source_id']}",
            f"Linkki: {it.get('url','')}",
            f"Perustelut: {'; '.join(it.get('reasons', []))}",
        ]))
        lines += [
            "BEGIN:VEVENT",
            f"UID:{it['id']}@lausuntovahti",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{d.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{(d + dt.timedelta(days=1)).strftime('%Y%m%d')}",
            f"SUMMARY:{ics_escape(summary)}",
            f"DESCRIPTION:{ics_escape(desc)}",
            f"URL:{it.get('url','')}",
            f"CATEGORIES:{ics_escape(it['priority'])},{ics_escape(it.get('type',''))}",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]
        count += 1
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n", count


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
    def sec(title, lst):
        if not lst:
            return f"### {title}\n_ei uusia_\n"
        return f"### {title}\n" + md_table(lst) + "\n"
    new_high = [i for i in new if i["priority"] == "high"]
    new_med = [i for i in new if i["priority"] == "medium"]
    new_low = [i for i in new if i["priority"] == "low"]
    upcoming = [i for i in items if i["priority"] in ("high", "medium") and parse_date(i.get("deadline"))
                and today <= parse_date(i["deadline"]) <= today + dt.timedelta(days=14)]
    nodl = [i for i in new if i["priority"] in ("high", "medium") and not i.get("deadline")]
    txt = f"""# Lausuntovahti – ajo {today.isoformat()}

Kalenterissa nyt {ics_count} tapahtumaa. Uusia: {len(new)} (high {len(new_high)}, medium {len(new_med)}, low {len(new_low)}). Päivitettyjä: {len(updated)}. Arkistoitu: {len(archived)}.

{sec("Uudet – HIGH (kalenteriin)", new_high)}
{sec("Uudet – MEDIUM (kalenteriin)", new_med)}
{sec("Määräpäivä seuraavan 14 päivän sisällä", upcoming)}
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
    if not rules:
        sys.exit("config/keywords.json missing")

    state_items = load_json(STATE / "items.json", [])
    archive = load_json(STATE / "archive.json", [])
    incoming = []
    if args.incoming:
        inc = load_json(Path(args.incoming), [])
        incoming = inc["items"] if isinstance(inc, dict) else inc

    items, new, updated = merge(state_items, incoming, today,
                                archived_ids=frozenset(a["id"] for a in archive))

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
    ICS_PATH.write_text(ics, encoding="utf-8")
    save_json(STATE / "items.json", sorted(items, key=lambda x: x.get("deadline") or "9999"))
    save_json(STATE / "archive.json", archive)

    (DOCS / "all-items.md").write_text(
        f"# Kaikki seurannassa olevat kohteet ({today.isoformat()})\n\n" + md_table(items) + "\n", encoding="utf-8")
    summary = write_summary(DOCS / f"summary-{today.isoformat()}.md", today, new, updated, items,
                            archived, args.failed, ics_count)
    print(summary)


if __name__ == "__main__":
    main()
