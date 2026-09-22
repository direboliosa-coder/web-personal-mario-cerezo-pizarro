#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

ORCID = "0000-0001-8097-0573"
OUTPUT = Path("data/publications-auto.json")
USER_AGENT = "MarioCerezoAcademicWebsite/1.0 (+https://direboliosa-coder.github.io/web-personal-mario-cerezo-pizarro/)"

OPENALEX_URL = "https://api.openalex.org/works"
ZENODO_URL = "https://zenodo.org/api/records"


def request_json(url: str, params: dict[str, object]) -> dict:
    query = urlencode(params, doseq=True)
    req = Request(
        f"{url}?{query}",
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    with urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def clean_text(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_doi(value: object) -> str:
    doi = clean_text(value)
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.I)
    return doi.rstrip("/").strip()


def title_key(value: object) -> str:
    text = clean_text(value).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def item_key(item: dict) -> str:
    doi = normalize_doi(item.get("doi"))
    if doi:
        return "doi:" + doi.lower()
    return "title:" + title_key(item.get("title"))


def classify(title: str, concepts: list[str] | None = None) -> str:
    x = " ".join([title] + (concepts or [])).lower()
    if re.search(r"geocach|earthcache|situad|informal|territor|media literacy|educaci[oó]n medi[aá]tica", x):
        return "l4"
    if re.search(r"realidad virtual|virtual reality|augmented|realidad aumentada|\b3d\b|stem|maker|fabricaci[oó]n|future classroom|aula del futuro|drone", x):
        return "l3"
    if re.search(r"videoj|video game|videogame|gaming|gamer|steam|assassin|cultural game|gamif|serious game|moss|forge of destiny", x):
        return "l1"
    return "l2"


def openalex_type(raw: object) -> str:
    t = clean_text(raw).lower()
    return {
        "article": "Artículo científico",
        "journal-article": "Artículo científico",
        "book-chapter": "Capítulo de libro",
        "book": "Libro",
        "dataset": "Conjunto de datos",
        "dissertation": "Tesis",
        "preprint": "Preprint",
        "review": "Reseña",
        "editorial": "Editorial",
        "letter": "Carta",
    }.get(t, clean_text(raw) or "Otro resultado")


def build_openalex_venue(work: dict) -> str:
    source = clean_text((((work.get("primary_location") or {}).get("source") or {}).get("display_name")))
    biblio = work.get("biblio") or {}
    volume = clean_text(biblio.get("volume"))
    issue = clean_text(biblio.get("issue"))
    first_page = clean_text(biblio.get("first_page"))
    last_page = clean_text(biblio.get("last_page"))

    details = []
    if volume and issue:
        details.append(f"{volume}({issue})")
    elif volume:
        details.append(volume)
    elif issue:
        details.append(f"({issue})")

    if first_page and last_page and first_page != last_page:
        details.append(f"{first_page}–{last_page}")
    elif first_page:
        details.append(first_page)

    if source and details:
        return f"{source}, " + ", ".join(details)
    return source or ", ".join(details)


def fetch_openalex() -> list[dict]:
    items: list[dict] = []
    cursor = "*"

    for _ in range(20):
        data = request_json(
            OPENALEX_URL,
            {
                "filter": f"author.orcid:{ORCID}",
                "per-page": 100,
                "cursor": cursor,
                "sort": "publication_date:desc",
            },
        )
        results = data.get("results") or []
        for work in results:
            title = clean_text(work.get("display_name") or work.get("title"))
            if not title:
                continue

            doi = normalize_doi(work.get("doi"))
            authors = "; ".join(
                clean_text(((authorship.get("author") or {}).get("display_name")))
                for authorship in (work.get("authorships") or [])
                if clean_text(((authorship.get("author") or {}).get("display_name")))
            )
            topics = [
                clean_text(topic.get("display_name"))
                for topic in (work.get("topics") or [])
                if clean_text(topic.get("display_name"))
            ]

            year = work.get("publication_year")
            url = clean_text(work.get("doi"))
            if not url:
                url = clean_text((work.get("primary_location") or {}).get("landing_page_url"))
            if not url:
                url = clean_text(work.get("id"))

            items.append(
                {
                    "year": year,
                    "title": title,
                    "authors": authors,
                    "venue": build_openalex_venue(work),
                    "url": url,
                    "doi": doi,
                    "type": openalex_type(work.get("type")),
                    "line": classify(title, topics),
                    "source": "OpenAlex",
                }
            )

        meta = data.get("meta") or {}
        next_cursor = meta.get("next_cursor")
        if not results or not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor

    return items


def zenodo_type(metadata: dict) -> str:
    resource = metadata.get("resource_type") or {}
    if isinstance(resource, dict):
        rtype = clean_text(resource.get("type")).lower()
        subtype = clean_text(resource.get("subtype")).lower()
    else:
        rtype = clean_text(metadata.get("upload_type")).lower()
        subtype = clean_text(metadata.get("publication_type")).lower()

    combo = f"{rtype} {subtype}"
    if "dataset" in combo:
        return "Conjunto de datos"
    if "software" in combo:
        return "Software"
    if "preprint" in combo:
        return "Preprint"
    if "thesis" in combo:
        return "Tesis"
    if "book" in combo and "chapter" in combo:
        return "Capítulo de libro"
    if "journal" in combo or "article" in combo:
        return "Artículo científico"
    if "presentation" in combo:
        return "Presentación"
    if "poster" in combo:
        return "Póster"
    return clean_text(subtype or rtype) or "Otro resultado"


def zenodo_license(metadata: dict) -> str:
    lic = metadata.get("license")
    if isinstance(lic, dict):
        return clean_text(lic.get("id") or lic.get("title"))
    return clean_text(lic)


def fetch_zenodo() -> list[dict]:
    items: list[dict] = []
    page = 1

    while page <= 20:
        data = request_json(
            ZENODO_URL,
            {
                "q": f'creators.orcid:"{ORCID}"',
                "size": 25,
                "page": page,
                "all_versions": "false",
                "sort": "mostrecent",
            },
        )
        hits = ((data.get("hits") or {}).get("hits")) or []
        for record in hits:
            metadata = record.get("metadata") or {}
            title = clean_text(metadata.get("title"))
            if not title:
                continue

            creators = metadata.get("creators") or []
            authors = "; ".join(
                clean_text(c.get("name"))
                for c in creators
                if isinstance(c, dict) and clean_text(c.get("name"))
            )
            doi = normalize_doi(metadata.get("doi") or record.get("doi"))
            pub_date = clean_text(metadata.get("publication_date") or record.get("created"))
            year = None
            match = re.search(r"\b(19|20)\d{2}\b", pub_date)
            if match:
                year = int(match.group(0))

            keywords = [clean_text(k) for k in (metadata.get("keywords") or []) if clean_text(k)]
            journal = metadata.get("journal") or {}
            venue = clean_text(journal.get("title") if isinstance(journal, dict) else "")
            if not venue:
                venue = "Zenodo"

            url = clean_text(((record.get("links") or {}).get("html")))
            if not url and doi:
                url = f"https://doi.org/{doi}"

            item = {
                "year": year,
                "title": title,
                "authors": authors,
                "venue": venue,
                "url": url,
                "doi": doi,
                "type": zenodo_type(metadata),
                "line": classify(title, keywords),
                "repository": "Zenodo",
                "source": "Zenodo",
            }
            lic = zenodo_license(metadata)
            if lic:
                item["license"] = lic
            items.append(item)

        if len(hits) < 25:
            break
        page += 1

    return items


def load_existing() -> list[dict]:
    if not OUTPUT.exists():
        return []
    try:
        data = json.loads(OUTPUT.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("items") or []
    except Exception:
        pass
    return []


def merge_items(existing: list[dict], incoming: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for item in existing:
        if isinstance(item, dict) and item.get("title"):
            merged[item_key(item)] = item

    for item in incoming:
        key = item_key(item)
        previous = merged.get(key, {})
        combined = dict(previous)
        combined.update({k: v for k, v in item.items() if v not in ("", None, [], {})})
        merged[key] = combined

    def sort_key(item: dict):
        year = item.get("year")
        try:
            year_num = int(year)
        except (TypeError, ValueError):
            year_num = 0
        return (-year_num, title_key(item.get("title")))

    return sorted(merged.values(), key=sort_key)


def main() -> int:
    existing = load_existing()
    incoming: list[dict] = []
    source_status: dict[str, dict] = {}

    for name, fetcher in (("OpenAlex", fetch_openalex), ("Zenodo", fetch_zenodo)):
        try:
            fetched = fetcher()
            incoming.extend(fetched)
            source_status[name] = {"ok": True, "count": len(fetched)}
            print(f"{name}: {len(fetched)} registros")
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            source_status[name] = {"ok": False, "error": str(exc)}
            print(f"{name}: error: {exc}", file=sys.stderr)
        except Exception as exc:
            source_status[name] = {"ok": False, "error": str(exc)}
            print(f"{name}: error inesperado: {exc}", file=sys.stderr)

    if not incoming and existing:
        print("No se obtuvieron novedades; se conserva el archivo automático existente.")
        return 0

    if not incoming and not existing:
        print("No se pudo obtener ninguna publicación y no existe un archivo previo.", file=sys.stderr)
        return 1

    items = merge_items(existing, incoming)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "orcid": ORCID,
        "sources": source_status,
        "policy": "append-and-update; never delete automatically; manual website records take precedence",
        "items": items,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Guardados {len(items)} resultados automáticos en {OUTPUT}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
