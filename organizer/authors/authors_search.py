#!/usr/bin/env python3
"""
authors_search.py
Kernsuche für Autor↔Autor und PDF↔Werk (Bipartit), mit Alias-JSONs.

Funktionen:
- Lädt Autoren-Aliase aus config/autoren_canon.json
- Optional: Lädt Werke/Serien/Generics aus config/works_canon.json
- Ermittelt Quell-Autor je PDF (Dateinamen-Aliase, Fallback Heuristik)
- Findet Ziel-Autor:innen im Text (Alias-Regex + Standardmuster)
- Findet Werke/Serien/Generics im Text (Regex aus works_canon.json)
- Aggregiert und exportiert:
    data/authors_data/session_*/authors_nodes.csv
    data/authors_data/session_*/authors_edges.csv
    data/authors_data/session_*/works_mentions.csv  (Spalten: pdf_file, canonical, kind, page, in_bib, pattern, context, weight)
- Schreibt GEXF und HTML-Report (Template in config/authors_report_template)

Annahmen/Defaults:
- Projektwurzel: /Users/programming/PycharmProjects/Find_Bibliography_NEw
- PDF-Basis:     <PROJECT_DIR>/data/azk_library
- Autoren-JSON:  <PROJECT_DIR>/config/autoren_canon.json
- Werke-JSON:    <PROJECT_DIR>/config/works_canon.json  (optional; wenn fehlt, wird Werk-Suche übersprungen)

Usage:
    Direkt ausführen. Keine Argumente. Pfade im Kopf anpassen bei Bedarf.
    Sichtbare Debug-Prints zeigen Fortschritt und Pfade.
"""
from __future__ import annotations

import os
import re
import json
from dataclasses import asdict, dataclass
from typing import Dict, List, Tuple, Iterable, Optional

from authors_utils import (
    Author, Mention,
    iter_pdfs, extract_authors_from_filenames,
    detect_bibliography_pages, extract_text_with_ocr, classify_section,
    strip_diacritics, compile_patterns, quick_page_filter, find_author_mentions, make_context,
    aggregate_edges, ensure_session_dir, write_csvs, write_gexf, write_html_report, write_session_meta,
    log_info, log_warn, log_error, environment_report, make_diacritic_regex
)

# ---- Konfiguration -----------------------------------------------------------
PROJECT_DIR = "/Users/programming/PycharmProjects/Find_Bibliography_NEw"
BASE_PDF_DIR = os.path.join(PROJECT_DIR, "data", "azk_library")
CANON_JSON_PATH = os.path.join(PROJECT_DIR, "config", "autoren_canon.json")
WORKS_JSON_PATH = os.path.join(PROJECT_DIR, "config", "works_canon.json")  # optional

# ---- Datentypen --------------------------------------------------------------
@dataclass
class WorkSpec:
    canonical: str
    kind: str                  # "work" | "series" | "generic"
    patterns: List[str]
    negatives: List[str]
    weight: float

@dataclass
class WorkMention:
    pdf_file: str
    canonical: str
    kind: str
    page: int
    in_bib: int
    pattern: str
    context: str
    weight: float


# ---- Autoren-Canon -----------------------------------------------------------
def load_authors_canon(path: str) -> Dict:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Autoren-Canon nicht gefunden: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if "authors" not in data or not isinstance(data["authors"], list):
        raise ValueError("autoren_canon.json: Feld 'authors' fehlt oder ist kein Array.")
    return data


def build_authors_from_canon(canon: Dict, heuristic_authors: Dict[str, Author]) -> Tuple[Dict[str, Author], Dict[str, dict], Dict[str, str]]:
    authors: Dict[str, Author] = {}
    aux: Dict[str, dict] = {}
    merge_map: Dict[str, str] = {}

    # Merge-Zuordnung
    for a in canon["authors"]:
        cid = a.get("canonical_id") or ""
        mi = a.get("merge_into")
        if mi and mi != cid:
            merge_map[cid] = mi

    # Nur kanonische (nicht-umzuleitende) IDs erzeugen
    def _tokens(cid: str) -> List[str]:
        return [x for x in cid.replace("_", "-").split("-") if x]

    def _surnames(tokens: List[str]) -> List[str]:
        out: List[str] = []
        if tokens:
            out.append(tokens[0])
            if tokens[-1] != tokens[0]:
                out.append(tokens[-1])
        return [s for s in out if len(s) > 1 and not s.isdigit()]

    for a in canon["authors"]:
        cid = a.get("canonical_id") or ""
        if not cid:
            continue
        if cid in merge_map and merge_map[cid] != cid:
            continue
        disp = a.get("display") or ", ".join(reversed([t.capitalize() for t in _tokens(cid)])) or cid
        surs = _surnames(_tokens(cid))
        pattern_strs: List[str] = []
        for sur in dict.fromkeys(surs):
            srx = make_diacritic_regex(sur)
            pat1 = rf"\b{srx}\b\s*,\s*[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\.\-]{{1,}}"
            pat2 = rf"\b[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\.\-]{{1,}}\s+{srx}\b"
            pat3 = rf"\b{srx}\b[^\n]{{0,30}}\b(1[4-9]\d{{2}}|20\d{{2}})\b"
            pattern_strs.extend([pat1, pat2, pat3])
        for ta in a.get("text_aliases", []) or []:
            if isinstance(ta, str) and ta:
                pattern_strs.append(ta)
        folded = tuple(strip_diacritics(s).casefold() for s in dict.fromkeys(surs))
        authors[cid] = Author(
            author_id=cid,
            display=disp,
            surnames=tuple(dict.fromkeys(surs)),
            pattern_strs=tuple(dict.fromkeys(pattern_strs)),
            folded_surnames=folded,
        )
        aux[cid] = {
            "negatives": list(a.get("negatives", []) or []),
            "filename_aliases": list(a.get("filename_aliases", []) or []),
            "text_aliases": list(a.get("text_aliases", []) or []),
        }

    # Heuristische Autoren ergänzen
    for hid, ha in heuristic_authors.items():
        if hid not in authors:
            authors[hid] = ha
            aux[hid] = {"negatives": [], "filename_aliases": [], "text_aliases": []}

    return authors, aux, merge_map


def compile_filename_aliases(aux: Dict[str, dict]) -> Dict[str, List[re.Pattern]]:
    out: Dict[str, List[re.Pattern]] = {}
    for aid, meta in aux.items():
        regs = []
        for rx in meta.get("filename_aliases", []):
            try:
                regs.append(re.compile(rx, re.IGNORECASE))
            except re.error as e:
                log_warn(f"Fehlerhafte filename_aliases-Regex für {aid}: {rx} ({e})")
        out[aid] = regs
    return out


def choose_src_from_filename(pdf_path: str, fname_rx: Dict[str, List[re.Pattern]], left_guess: str) -> Optional[str]:
    for aid, regs in fname_rx.items():
        for r in regs:
            if r.search(pdf_path):
                return aid
    return left_guess or None


# ---- Werke-Canon -------------------------------------------------------------
def load_works_canon(path: str) -> Optional[Dict]:
    if not os.path.isfile(path):
        log_warn(f"Werke-Canon fehlt, Werk-Suche wird übersprungen: {path}")
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if "works" not in data:
        raise ValueError("works_canon.json: Feld 'works' fehlt.")
    return data


def build_work_specs(work_json: Dict) -> List[WorkSpec]:
    weights = {"work": 1.0, "series": 1.0, "generic": 1.2}
    weights.update(work_json.get("weights", {}))

    specs: List[WorkSpec] = []
    # Einzelwerke
    for w in work_json.get("works", []):
        canon = w.get("canonical") or ""
        aliases = list(w.get("aliases", []) or [])
        if not canon or not aliases:
            continue
        specs.append(WorkSpec(canonical=canon, kind="work", patterns=aliases, negatives=list(w.get("negatives", [])), weight=float(weights.get("work", 1.0))))
    # Serien
    for s in work_json.get("series", []):
        canon = s.get("canonical") or ""
        aliases = list(s.get("aliases", []) or [])
        if not canon or not aliases:
            continue
        specs.append(WorkSpec(canonical=canon, kind="series", patterns=aliases, negatives=list(s.get("negatives", [])), weight=float(weights.get("series", 1.0))))
    # Generische Muster
    for g in work_json.get("generic_terms", []):
        canon = g.get("label") or ""
        aliases = list(g.get("patterns", []) or [])
        if not canon or not aliases:
            continue
        specs.append(WorkSpec(canonical=canon, kind="generic", patterns=aliases, negatives=list(g.get("negatives", [])), weight=float(weights.get("generic", 1.2))))
    return specs


def compile_work_regex(specs: List[WorkSpec]) -> Tuple[List[WorkSpec], Dict[str, List[re.Pattern]], Dict[str, List[re.Pattern]]]:
    pos: Dict[str, List[re.Pattern]] = {}
    neg: Dict[str, List[re.Pattern]] = {}
    for sp in specs:
        ps: List[re.Pattern] = []
        for pat in sp.patterns:
            try:
                ps.append(re.compile(pat, re.IGNORECASE))
            except re.error as e:
                log_warn(f"Fehlerhafte Werk-Regex für {sp.canonical}: {pat} ({e})")
        pos[sp.canonical] = ps
        neg[sp.canonical] = [re.compile(n, re.IGNORECASE) for n in (sp.negatives or [])]
    return specs, pos, neg


# ---- Worker ------------------------------------------------------------------
def _process_pdf(pdf_path: str,
                 src_id: str,
                 authors_plain: Dict[str, dict],
                 work_bundle: Optional[Tuple[List[WorkSpec], Dict[str, List[re.Pattern]], Dict[str, List[re.Pattern]]]]
                 ) -> Tuple[List[Mention], List[WorkMention]]:
    mentions: List[Mention] = []
    work_hits: List[WorkMention] = []

    try:
        import fitz  # local import
        doc = fitz.open(pdf_path)
    except Exception as e:
        log_warn(f"Konnte PDF nicht öffnen: {pdf_path} ({e})")
        return mentions, work_hits

    try:
        bib_pages = detect_bibliography_pages(doc)
        compiled_cache: Dict[str, List[re.Pattern]] = {}
        negatives_cache: Dict[str, List[re.Pattern]] = {}

        for pidx in range(doc.page_count):
            page = doc.load_page(pidx)
            txt = extract_text_with_ocr(page)
            if not txt:
                continue
            section_bib = classify_section(pidx, bib_pages) == "bib"
            txt_folded = strip_diacritics(txt).casefold()

            # --- Ziel-Autor:innen ---
            for tgt_id, a_dict in authors_plain.items():
                folded_surnames = tuple(a_dict["folded_surnames"])
                if not quick_page_filter(txt_folded, folded_surnames):
                    continue
                if tgt_id not in compiled_cache:
                    compiled_cache[tgt_id] = compile_patterns(a_dict["pattern_strs"])
                    negatives_cache[tgt_id] = [re.compile(n, re.IGNORECASE) for n in a_dict.get("negatives", []) or []]
                raw_hits = find_author_mentions(txt, compiled_cache[tgt_id])
                if not raw_hits:
                    continue
                seen = set()
                for span, pat in raw_hits:
                    if span in seen:
                        continue
                    seen.add(span)
                    ctx = make_context(txt, span)
                    if any(n.search(ctx) for n in negatives_cache.get(tgt_id, [])):
                        continue
                    mentions.append(Mention(
                        src_id=src_id,
                        tgt_id=tgt_id,
                        pdf_path=pdf_path,
                        page=pidx + 1,
                        in_bib=section_bib,
                        pattern=pat,
                        context=ctx
                    ))

            # --- Werke/Serien/Generics ---
            if work_bundle:
                specs, pos_map, neg_map = work_bundle
                for sp in specs:
                    for rex in pos_map.get(sp.canonical, []):
                        for m in rex.finditer(txt):
                            span = m.span()
                            ctx = make_context(txt, span)
                            if any(n.search(ctx) for n in neg_map.get(sp.canonical, [])):
                                continue
                            work_hits.append(WorkMention(
                                pdf_file=pdf_path,
                                canonical=sp.canonical,
                                kind=sp.kind,
                                page=pidx + 1,
                                in_bib=int(section_bib),
                                pattern=rex.pattern,
                                context=ctx,
                                weight=float(sp.weight)
                            ))
    finally:
        try:
            doc.close()
        except Exception:
            pass
    return mentions, work_hits


# ---- Orchestrierung ----------------------------------------------------------
def run_on_folder(base: str) -> str:
    environment_report()

    pdfs = list(iter_pdfs(base))
    if not pdfs:
        raise RuntimeError("Keine PDFs gefunden.")

    log_info(f"PDFs gefunden: {len(pdfs)}")

    # Heuristische Autoren aus Dateinamen (Backfill)
    heur_authors = extract_authors_from_filenames(pdfs)
    canon = load_authors_canon(CANON_JSON_PATH)
    authors, aux, merge_map = build_authors_from_canon(canon, heur_authors)
    log_info(f"Autoren erkannt: {len(authors)}")
    for aid, a in authors.items():
        print(f"   - {aid} → {a.display} | Nachnamen: {', '.join(a.surnames)}")

    # Filename-Aliase
    fname_rx = compile_filename_aliases(aux)

    # Werk-Specs
    work_json = load_works_canon(WORKS_JSON_PATH)
    work_bundle: Optional[Tuple[List[WorkSpec], Dict[str, List[re.Pattern]], Dict[str, List[re.Pattern]]]] = None
    if work_json:
        specs = build_work_specs(work_json)
        work_bundle = compile_work_regex(specs)
        log_info(f"Werk-Suche aktiv: Spezifikationen={len(specs)}")

    # Quelle pro Datei
    file2author: Dict[str, str] = {}
    for p in pdfs:
        left = os.path.splitext(os.path.basename(p))[0].split("__")[0].replace("_", "-").strip("-")
        chosen = choose_src_from_filename(p, fname_rx, left)
        chosen = merge_map.get(chosen, chosen)
        if not chosen or chosen not in authors:
            log_warn(f"Kein gültiger Quell-Autor für {os.path.basename(p)} erkannt, heuristisch: {left}")
            if left in authors:
                chosen = left
            else:
                continue
        file2author[p] = chosen

    # Tasks
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from dataclasses import asdict as _asdict
    def _pack_targets() -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for tid, ta in authors.items():
            d = _asdict(ta)
            out[tid] = {
                "author_id": d["author_id"],
                "display": d["display"],
                "surnames": list(d["surnames"]),
                "pattern_strs": list(d["pattern_strs"]),
                "folded_surnames": list(d["folded_surnames"]),
                "negatives": list(aux.get(tid, {}).get("negatives", [])),
            }
        return out

    targets_all = _pack_targets()
    tasks: List[Tuple[str, str]] = []
    for pdf in pdfs:
        src_id = file2author.get(pdf)
        if not src_id:
            continue
        tasks.append((pdf, src_id))

    mentions_all: List[Mention] = []
    work_hits_all: List[WorkMention] = []
    if not tasks:
        log_warn("Keine verarbeitbaren PDFs. Abbruch.")
    else:
        cpu = max(1, os.cpu_count() or 1)
        log_info(f"Starte Parallelverarbeitung mit {cpu} Prozessen.")
        with ProcessPoolExecutor(max_workers=cpu) as ex:
            futs = {ex.submit(_process_pdf, pdf, src, targets_all, work_bundle): (pdf, src) for (pdf, src) in tasks}
            done = 0; total = len(futs)
            for fut in as_completed(futs):
                done += 1
                pdf, src = futs[fut]
                try:
                    a_hits, w_hits = fut.result()
                    mentions_all.extend(a_hits)
                    work_hits_all.extend(w_hits)
                except Exception as e:
                    log_warn(f"Fehler bei {pdf}: {e}")
                if done % 1 == 0:
                    log_info(f"Fortschritt: {done}/{total}")

    # Ausgabe-Ordner
    session_dir = ensure_session_dir(base, pdfs)
    log_info(f"Session-Ordner: {session_dir}")

    # Aggregation Autoren
    from authors_utils import HAVE_PANDAS  # check flag
    if not HAVE_PANDAS:
        raise RuntimeError("pandas fehlt. Installiere mit: pip install pandas")

    df_nodes, df_edges = aggregate_edges(authors, mentions_all)
    write_session_meta(session_dir, base, pdfs)
    write_csvs(session_dir, df_nodes, df_edges)
    write_gexf(session_dir, authors, df_edges)
    write_html_report(session_dir, authors, df_edges)

    # Export Werke-Mentions
    if work_hits_all:
        import pandas as pd  # type: ignore
        rows = [{
            "pdf_file": wh.pdf_file,
            "canonical": wh.canonical,
            "kind": wh.kind,
            "page": wh.page,
            "in_bib": int(wh.in_bib),
            "pattern": wh.pattern,
            "context": wh.context,
            "weight": float(wh.weight)
        } for wh in work_hits_all]
        dfw = pd.DataFrame(rows)
        out_path = os.path.join(session_dir, "works_mentions.csv")
        dfw.to_csv(out_path, index=False, sep=";")
        log_info(f"Werk-Mentions geschrieben: {out_path}")
        # Kurzfeedback
        topw = dfw.groupby("canonical")["weight"].sum().sort_values(ascending=False).head(10)
        if not topw.empty:
            log_info("Top-Werke:")
            for k, v in topw.items():
                print(f"  {k}  score={int(v)}")

    # Kurzsummary Autoren
    if not df_edges.empty:
        log_info("Top-Kanten:")
        for _, r in df_edges.sort_values("weighted", ascending=False).head(10).iterrows():
            try:
                sdisp = authors[r["src"]].display
                tdisp = authors[r["tgt"]].display
            except KeyError:
                sdisp = r["src"]; tdisp = r["tgt"]
            print(f"  {sdisp} → {tdisp} | total={r['total']} bib={r['bib_hits']} text={r['text_hits']}")
    else:
        log_warn("Keine Autoren-Kanten gefunden.")
    log_info("Fertig.")
    return session_dir


def main() -> None:
    log_info(f"Autor↔Autor + Werk-Suche startet… Ordner: {BASE_PDF_DIR}")
    try:
        run_on_folder(BASE_PDF_DIR)
    except Exception as e:
        log_error(f"Abbruch: {e}")


if __name__ == "__main__":
    main()