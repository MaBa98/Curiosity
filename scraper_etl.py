#!/usr/bin/env python3
"""ETL per Curiosity: estrae curiosità da Wikipedia e aggiorna curiosities_db.json.

Uso:
    python scraper_etl.py --batch-size 20 --lang it
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

DB_PATH = Path(__file__).with_name("curiosities_db.json")
DB_VERSION = 1
MIN_EXTRACT_CHARS = 350
USER_AGENT = (
    "CuriosityETL/1.0 (https://github.com/MaBa98/Curiosity; "
    "progetto educativo di curiosita' da Wikipedia) python-requests"
)

CATEGORIES = {
    "Finanza": [
        "Crisi_finanziarie", "Bolla_speculativa", "Storia_della_banca",
        "Derivati", "Teoria_dei_giochi",
    ],
    "Storia": [
        "Storia_economica", "Invenzioni", "Scoperte_scientifiche",
        "Trattati_internazionali", "Civiltà_antiche",
    ],
    "Scienza": [
        "Paradossi", "Fisica_quantistica", "Astronomia",
        "Effetti_fisici", "Matematica_ricreativa",
    ],
}

DISAMBIG_RE = re.compile(r"(può riferirsi a|puo riferirsi a|may refer to|disambigua)", re.I)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Þ\"«“'(])")
# abbreviazioni comuni che non chiudono la frase
ABBREV_RE = re.compile(r"\b(?:ad es|es|ecc|cfr|n|nn|sig|dott|prof|ca|circa|a\.C|d\.C|S|Sr|Jr|St|vs)\.$", re.I)


def api_get(session: requests.Session, url: str, params: dict | None = None,
            retries: int = 5) -> dict | None:
    for attempt in range(retries):
        try:
            time.sleep(0.4)  # cortesia verso le API Wikimedia
            r = session.get(url, params=params, timeout=30)
            if r.status_code == 404:
                return None
            if r.status_code == 429 or r.status_code >= 500:
                try:
                    wait = int(r.headers.get("Retry-After", ""))
                except ValueError:
                    wait = 5 * (attempt + 1)
                time.sleep(min(wait, 60))
                continue
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as exc:
            if attempt == retries - 1:
                print(f"  ! errore su {url}: {exc}", file=sys.stderr)
                return None
            time.sleep(2 * (attempt + 1))
    return None


def load_db(path: Path, lang: str) -> dict:
    if path.exists():
        try:
            db = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(db, dict) and isinstance(db.get("items"), list):
                return db
        except json.JSONDecodeError:
            print("! DB esistente non valido, ricreo", file=sys.stderr)
    return {"version": DB_VERSION, "updated_at": None, "items": []}


def category_members(session, lang: str, cat: str) -> list[dict]:
    """Pagine (namespace 0) di una categoria."""
    api = f"https://{lang}.wikipedia.org/w/api.php"
    out, cont = [], {}
    while True:
        params = {
            "action": "query", "list": "categorymembers", "format": "json",
            "cmtitle": f"Categoria:{cat}" if lang == "it" else f"Category:{cat}",
            "cmtype": "page", "cmnamespace": 0, "cmlimit": 100, **cont,
        }
        data = api_get(session, api, params)
        if not data:
            break
        out.extend(data.get("query", {}).get("categorymembers", []))
        cont = data.get("continue", {})
        if not cont or len(out) >= 100:
            break
    return out


def fetch_extracts(session, lang: str, pageids: list[int]) -> dict[int, dict]:
    """Estratti in testo semplice + categorie + pageprops (max 20 per chiamata)."""
    api = f"https://{lang}.wikipedia.org/w/api.php"
    params = {
        "action": "query", "format": "json", "prop": "extracts|categories|pageprops",
        "exintro": 1, "explaintext": 1, "exlimit": "max", "cllimit": "max",
        "ppprop": "disambiguation", "pageids": "|".join(map(str, pageids)),
    }
    data = api_get(session, api, params)
    return (data or {}).get("query", {}).get("pages", {}) if data else {}


def fetch_summary(session, lang: str, title: str) -> dict | None:
    safe = requests.utils.quote(title.replace(" ", "_"), safe="")
    return api_get(session, f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{safe}")


def split_sentences(text: str) -> list[str]:
    parts = SENTENCE_RE.split(text.strip())
    sentences: list[str] = []
    for p in parts:
        if sentences and ABBREV_RE.search(sentences[-1]):
            sentences[-1] += " " + p
        else:
            sentences.append(p)
    return sentences


def clean(text: str) -> str:
    text = re.sub(r"\[\d+\]|\[senza fonte\]|\[non chiaro\]", "", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def is_stub_or_disambig(page: dict, summary: dict, text: str) -> bool:
    if "disambiguation" in page.get("pageprops", {}):
        return True
    if summary.get("type") == "disambiguation":
        return True
    if any("stub" in c.get("title", "").lower() or "disambigua" in c.get("title", "").lower()
           for c in page.get("categories", [])):
        return True
    return bool(DISAMBIG_RE.search(text[:200]))


def build_item(category: str, page: dict, summary: dict, lang: str) -> dict | None:
    text = clean(page.get("extract") or summary.get("extract") or "")
    if len(text) < MIN_EXTRACT_CHARS or is_stub_or_disambig(page, summary, text):
        return None
    sentences = split_sentences(text.replace("\n", " "))
    if len(sentences) < 2:
        return None
    hook = sentences[0]
    # micro_body: prime frasi successive fino a ~320 caratteri
    micro, used = "", 1
    for s in sentences[1:]:
        if micro and len(micro) + len(s) > 320:
            break
        micro = f"{micro} {s}".strip()
        used += 1
    deep = " ".join(sentences[used:]).strip()
    # preferisci i paragrafi originali per il deep dive
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    if len(paragraphs) > 1:
        deep = "\n\n".join(paragraphs[1:]) if not deep else deep
    if not deep:
        deep = micro
    source = (summary.get("content_urls", {}).get("desktop", {}).get("page")
              or f"https://{lang}.wikipedia.org/wiki/{page['title'].replace(' ', '_')}")
    image = (summary.get("originalimage") or {}).get("source")
    return {
        "id": str(page["pageid"]),
        "category": category,
        "title": page["title"],
        "hook_title": hook,
        "micro_body": micro,
        "deep_dive_expandable": deep,
        "source_url": source,
        "image_url": image,
        "lang": lang,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def run(batch_size: int, lang: str, db_path: Path) -> int:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    db = load_db(db_path, lang)
    seen = {str(i["id"]) for i in db["items"]}

    # candidati per macro-categoria, esclusi i già presenti
    pools: dict[str, list[dict]] = {}
    for macro, cats in CATEGORIES.items():
        pool, ids = [], set()
        for cat in cats:
            for m in category_members(session, lang, cat):
                pid = str(m["pageid"])
                if pid not in seen and pid not in ids:
                    ids.add(pid)
                    pool.append(m)
            time.sleep(0.2)
        pools[macro] = pool
        print(f"[{macro}] {len(pool)} candidati nuovi")

    added: list[dict] = []
    rejected = 0
    # round-robin tra macro-categorie, in blocchi da 20 per le extracts
    while len(added) < batch_size and any(pools.values()):
        for macro in CATEGORIES:
            if len(added) >= batch_size:
                break
            chunk = [pools[macro].pop(0) for _ in range(min(4, len(pools[macro])))]
            if not chunk:
                continue
            pages = fetch_extracts(session, lang, [c["pageid"] for c in chunk])
            for c in chunk:
                if len(added) >= batch_size:
                    break
                page = pages.get(str(c["pageid"]))
                if not page or len(clean(page.get("extract", ""))) < MIN_EXTRACT_CHARS:
                    rejected += 1
                    continue
                summary = fetch_summary(session, lang, page["title"]) or {}
                item = build_item(macro, page, summary, lang)
                time.sleep(0.2)
                if item is None or item["id"] in seen:
                    rejected += 1
                    continue
                seen.add(item["id"])
                added.append(item)
                print(f"  + [{macro}] {item['title']}")

    db["items"].extend(added)
    db["version"] = DB_VERSION
    if added or not db.get("updated_at"):
        db["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db_path.write_text(json.dumps(db, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Aggiunte {len(added)} curiosità, scartate {rejected}; totale {len(db['items'])}")
    return len(added)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch-size", type=int, default=20, help="nuove curiosità per esecuzione")
    ap.add_argument("--lang", default="it", help="edizione Wikipedia (default: it)")
    ap.add_argument("--db", type=Path, default=DB_PATH, help="percorso del JSON")
    args = ap.parse_args()
    run(args.batch_size, args.lang, args.db)


if __name__ == "__main__":
    main()
