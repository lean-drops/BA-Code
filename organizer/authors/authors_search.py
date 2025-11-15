#!/usr/bin/env python3
"""
authors_search.py

Kernsuche für Autor↔Autor und PDF↔Werk (Bipartit), mit Unterstützung für
SQLite-basierte Autoren- und Werkkonfigurationen. Zusätzlich zu den
Einträgen aus der Datenbank werden heuristisch aus den Dateinamen erkannte
Autoren ergänzt, und optional weitere Muster aus works_canon.json. Dadurch
werden auch neue Einträge berücksichtigt, die dem Schema entsprechen.

Der Ablauf entspricht im Wesentlichen dem bisherigen JSON-basierten
authors_search.py, nutzt aber eine zentrale SQLite-Datei als
„Single Source of Truth“ und ergänzt fehlende Einträge.

Outputs:
  - authors_nodes.csv, authors_edges.csv, works_mentions.csv im
    Session-Ordner (wie zuvor)
  - edges_aa in der SQLite-DB (Author→Author Kanten)
  - bibliography_works in der SQLite-DB (Verknüpfung PDF→Werk)

Usage: direkt ausführen. Pfade im Kopf anpassen oder über environment
variablen konfigurieren. Standardmäßig wird <PROJECT_DIR>/data/test als
PDF-Ordner verwendet und <PROJECT_DIR>/config/chroniken.sqlite3 als DB.
"""
from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass, asdict
from typing import Dict, Iterable, List, Optional, Tuple
from concurrent.futures import ProcessPoolExecutor, as_completed

from organizer.authors.authors_utils import (
    Author,
    Mention,
    iter_pdfs,
    extract_authors_from_filenames,
    detect_bibliography_pages,
    extract_text_with_ocr,
    classify_section,
    strip_diacritics,
    compile_patterns,
    quick_page_filter,
    find_author_mentions,
    make_context,
    aggregate_edges,
    ensure_session_dir,
    write_csvs,
    write_gexf,
    write_html_report,
    write_session_meta,
    log_info,
    log_warn,
    log_error,
    environment_report,
    load_works_canon,
    build_work_specs,
    compile_work_regex,
)

# DB-Hilfsfunktionen
from organizer.authors.db_io import (
    fetch_authors,
    fetch_work_specs,
    create_search_run,
    insert_edges_aa,
    ensure_bibliography_entry,
    ensure_bibliography_work,
)


@dataclass
class WorkSpec:
    """Repräsentiert ein Werk bzw. Muster für die Suche."""
    canonical: str
    kind: str  # "work" | "series" | "generic"
    patterns: List[str]
    negatives: List[str]
    weight: float


@dataclass
class WorkMention:
    """Treffer eines Werk-Musters in einem PDF."""
    pdf_file: str
    canonical: str
    kind: str
    page: int
    in_bib: int
    pattern: str
    context: str
    weight: float


def _process_pdf(
    pdf_path: str,
    src_id: str,
    authors_plain: Dict[str, dict],
    work_bundle: Optional[
        Tuple[List[WorkSpec], Dict[str, List[re.Pattern]], Dict[str, List[re.Pattern]]]
    ],
) -> Tuple[List[Mention], List[WorkMention]]:
    """Extrahiert Author- und Werk-Mentions aus einem einzelnen PDF.

    Args:
        pdf_path: Absoluter Pfad zum PDF
        src_id: Canonical-ID des Quell-Autors (aus Filename)
        authors_plain: Mapping Author-ID -> Dict mit Pattern/Negatives
        work_bundle: WorkSpec + pre-kompilierte Regex-Maps (optional)

    Returns:
        Liste von Author-Mentions, Liste von Werk-Mentions
    """
    mentions: List[Mention] = []
    work_hits: List[WorkMention] = []
    try:
        import fitz  # type: ignore  # lazy import
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

            # Ziel-Autor:innen finden
            for tgt_id, a_dict in authors_plain.items():
                folded_surnames = tuple(a_dict["folded_surnames"])
                if not quick_page_filter(txt_folded, folded_surnames):
                    continue
                if tgt_id not in compiled_cache:
                    compiled_cache[tgt_id] = compile_patterns(a_dict["pattern_strs"])
                    negatives_cache[tgt_id] = [
                        re.compile(n, re.IGNORECASE) for n in a_dict.get("negatives", []) or []
                    ]
                raw_hits = find_author_mentions(txt, compiled_cache[tgt_id])
                if not raw_hits:
                    continue
                seen = set()
                for span, pat in raw_hits:
                    if span in seen:
                        continue
                    seen.add(span)
                    ctx = make_context(txt, span)
                    # Ausschlussmuster
                    if any(n.search(ctx) for n in negatives_cache.get(tgt_id, [])):
                        continue
                    mentions.append(
                        Mention(
                            src_id=src_id,
                            tgt_id=tgt_id,
                            pdf_path=pdf_path,
                            page=pidx + 1,
                            in_bib=section_bib,
                            pattern=pat,
                            context=ctx,
                        )
                    )

            # Werke (works/series/generic) erkennen
            if work_bundle:
                specs, pos_map, neg_map = work_bundle
                for sp in specs:
                    for rex in pos_map.get(sp.canonical, []):
                        for m in rex.finditer(txt):
                            span = m.span()
                            ctx = make_context(txt, span)
                            if any(
                                n.search(ctx) for n in neg_map.get(sp.canonical, [])
                            ):
                                continue
                            work_hits.append(
                                WorkMention(
                                    pdf_file=pdf_path,
                                    canonical=sp.canonical,
                                    kind=sp.kind,
                                    page=pidx + 1,
                                    in_bib=int(section_bib),
                                    pattern=rex.pattern,
                                    context=ctx,
                                    weight=float(sp.weight),
                                )
                            )
    finally:
        try:
            doc.close()
        except Exception:
            pass
    return mentions, work_hits


def run_on_folder(base: str) -> str:
    """Führt die komplette Autor↔Autor/Werk-Suche auf einem Ordner aus."""
    environment_report()

    pdfs = list(iter_pdfs(base))
    if not pdfs:
        raise RuntimeError("Keine PDFs gefunden.")
    log_info(f"PDFs gefunden: {len(pdfs)}")

    # Heuristische Autoren aus Dateinamen
    heur_authors = extract_authors_from_filenames(pdfs)

    # DB-Pfad ermitteln
    project_dir = os.path.dirname(os.path.dirname(base)) if os.path.isdir(base) else os.getcwd()
    db_path = os.path.join(project_dir, "config", "chroniken.sqlite3")

    # Autoren und Meta aus DB laden und heuristische Autoren integrieren
    with sqlite3.connect(db_path) as conn:
        authors_db, aux_db, merge_map = fetch_authors(conn)
        for hid, ha in heur_authors.items():
            if hid not in authors_db:
                authors_db[hid] = ha
                aux_db[hid] = {"negatives": [], "filename_aliases": [], "text_aliases": []}
        authors = authors_db
        aux = aux_db
        # Werk-Spezifikationen aus DB
        db_specs = fetch_work_specs(conn)
        specs: List[WorkSpec] = [
            WorkSpec(
                canonical=s.canonical,
                kind=s.kind,
                patterns=list(s.patterns),
                negatives=list(s.negatives),
                weight=float(s.weight),
            )
            for s in db_specs
        ]

    # Zusätzlich works_canon.json laden (für Serien/Generics)
    work_json: Optional[dict] = None
    works_json_path = os.path.join(project_dir, "config", "works_canon.json")
    if os.path.isfile(works_json_path):
        try:
            work_json = load_works_canon(works_json_path)
        except Exception:
            work_json = None
    if work_json:
        specs_json = build_work_specs(work_json)
        specs.extend(specs_json)

    # Autoren ausgeben
    log_info(f"Autoren erkannt: {len(authors)}")
    for aid, a in authors.items():
        try:
            print(f"   - {aid} → {a.display} | Nachnamen: {', '.join(a.surnames)}")
        except Exception:
            print(f"   - {aid}")

    # Filename-Aliase übersetzen
    from organizer.authors.authors_utils import compile_filename_aliases, choose_src_from_filename
    fname_rx = compile_filename_aliases(aux)

    # Werk-Regex kompilieren
    work_bundle: Optional[
        Tuple[List[WorkSpec], Dict[str, List[re.Pattern]], Dict[str, List[re.Pattern]]]
    ] = None
    if specs:
        work_bundle = compile_work_regex(specs)
        log_info(f"Werk-Suche aktiv: Spezifikationen={len(specs)}")

    # Quelle pro Datei bestimmen
    file2author: Dict[str, str] = {}
    for p in pdfs:
        left = (
            os.path.splitext(os.path.basename(p))[0]
            .split("__")[0]
            .replace("_", "-")
            .strip("-")
        )
        chosen = choose_src_from_filename(p, fname_rx, left)
        chosen = merge_map.get(chosen, chosen)
        if not chosen or chosen not in authors:
            log_warn(
                f"Kein gültiger Quell-Autor für {os.path.basename(p)} erkannt, heuristisch: {left}"
            )
            if left in authors:
                chosen = left
            else:
                continue
        file2author[p] = chosen

    # Targets-Struktur für Multiprocessing bauen
    targets_all: Dict[str, dict] = {
        tid: {
            "author_id": a.author_id,
            "display": a.display,
            "surnames": list(a.surnames),
            "pattern_strs": list(a.pattern_strs),
            "folded_surnames": list(a.folded_surnames),
            "negatives": list(aux.get(tid, {}).get("negatives", [])),
        }
        for tid, a in authors.items()
    }

    # Aufgabenliste
    tasks: List[Tuple[str, str]] = [
        (pdf, file2author[pdf]) for pdf in pdfs if pdf in file2author
    ]

    # Ergebnisse sammeln
    mentions_all: List[Mention] = []
    work_hits_all: List[WorkMention] = []
    if not tasks:
        log_warn("Keine verarbeitbaren PDFs. Abbruch.")
    else:
        # Begrenze die Anzahl der Worker auf die Anzahl der Aufgaben für weniger Overhead
        max_workers = max(1, min(len(tasks), os.cpu_count() or 1))
        log_info(f"Starte Parallelverarbeitung mit {max_workers} Prozessen.")
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            futs = {
                ex.submit(_process_pdf, pdf, src, targets_all, work_bundle): (pdf, src)
                for (pdf, src) in tasks
            }
            done = 0
            total = len(futs)
            for fut in as_completed(futs):
                done += 1
                pdf, src = futs[fut]
                try:
                    a_hits, w_hits = fut.result()
                    if a_hits:
                        mentions_all.extend(a_hits)
                    if w_hits:
                        work_hits_all.extend(w_hits)
                except Exception as e:
                    log_warn(f"Fehler bei {pdf}: {e}")
                # Logge den Fortschritt seltener für mehr Übersicht
                if done % 5 == 0 or done == total:
                    log_info(f"Fortschritt: {done}/{total}")

    # Session-Verzeichnis
    session_dir = ensure_session_dir(base, pdfs)
    log_info(f"Session-Ordner: {session_dir}")

    # Ergebnisse aggregieren (Author-Kanten)
    import pandas as pd  # type: ignore
    df_nodes, df_edges = aggregate_edges(authors, mentions_all)
    write_session_meta(session_dir, base, pdfs)
    write_csvs(session_dir, df_nodes, df_edges)
    write_gexf(session_dir, authors, df_edges)
    write_html_report(session_dir, authors, df_edges)

    # Works CSV schreiben
    if work_hits_all:
        rows = [
            {
                "pdf_file": wh.pdf_file,
                "canonical": wh.canonical,
                "kind": wh.kind,
                "page": wh.page,
                "in_bib": int(wh.in_bib),
                "pattern": wh.pattern,
                "context": wh.context,
                "weight": float(wh.weight),
            }
            for wh in work_hits_all
        ]
        dfw = pd.DataFrame(rows)
        out_path = os.path.join(session_dir, "works_mentions.csv")
        dfw.to_csv(out_path, index=False, sep=";")
        log_info(f"Werk-Mentions geschrieben: {out_path}")
        topw = (
            dfw.groupby("canonical")["weight"].sum().sort_values(ascending=False).head(10)
        )
        if not topw.empty:
            log_info("Top-Werke:")
            for k, v in topw.items():
                print(f"  {k}  score={int(v)}")
    else:
        log_info("Keine Werk-Mentions gefunden.")

    # Kanten in DB (edges_aa) und bibliography_works ergänzen
    with sqlite3.connect(db_path) as conn:
        # Run-ID: entweder von außen (SEARCH_RUN_ID) oder neu anlegen
        env_run = os.environ.get("SEARCH_RUN_ID")
        if env_run:
            try:
                run_id = int(env_run)
                log_info(f"Nutze externen search_run.id={run_id} (SEARCH_RUN_ID).")
            except ValueError:
                log_warn(f"Ungültige SEARCH_RUN_ID='{env_run}', erzeuge neuen Lauf.")
                run_id = create_search_run(conn, kind="authors_search", session_dir=session_dir)
        else:
            run_id = create_search_run(conn, kind="authors_search", session_dir=session_dir)

        inserted = insert_edges_aa(conn, run_id, file2author, mentions_all, project_dir)
        log_info(f"edges_aa eingefügt: {inserted}")
        if work_hits_all:
            # Caching, um Bibliography-IDs nicht mehrfach nachzuschlagen
            bib_cache: Dict[str, int] = {}
            for wh in work_hits_all:
                if wh.pdf_file not in bib_cache:
                    bib_cache[wh.pdf_file] = ensure_bibliography_entry(
                        conn, project_dir, wh.pdf_file, file2author.get(wh.pdf_file, "")
                    )
                ensure_bibliography_work(conn, bib_cache[wh.pdf_file], wh.canonical)
            log_info("bibliography_works aktualisiert.")

    # Kurzsummary Autoren
    if not df_edges.empty:
        log_info("Top-Kanten:")
        for _, r in (
            df_edges.sort_values("weighted", ascending=False).head(10).iterrows()
        ):
            src_id = r[1]["src"]
            tgt_id = r[1]["tgt"]
            sdisp = authors.get(src_id).display if src_id in authors else src_id
            tdisp = authors.get(tgt_id).display if tgt_id in authors else tgt_id
            print(
                f"  {sdisp} → {tdisp} | total={r[1]['total']} bib={r[1]['bib_hits']} text={r[1]['text_hits']}"
            )
    else:
        log_warn("Keine Autoren-Kanten gefunden.")
    log_info("Fertig.")
    return session_dir


def main() -> None:
    """Startpunkt für die Autor↔Autor + Werk-Suche."""
    # Standard-PDF-Ordner ist unterhalb des Projektverzeichnisses
    project_dir = os.getcwd()
    base_pdf_dir = os.path.join(project_dir, "data", "test")
    if not os.path.isdir(base_pdf_dir):
        # Fallback: versuche detect_project_dir zu verwenden
        try:
            from organizer.authors.authors_utils import detect_project_dir as _detect
            project_dir = _detect()
            base_pdf_dir = os.path.join(project_dir, "data", "test")
        except Exception:
            pass
    log_info(f"Autor↔Autor + Werk-Suche startet… Ordner: {base_pdf_dir}")
    try:
        run_on_folder(base_pdf_dir)
    except Exception as e:
        log_error(f"Abbruch: {e}")


if __name__ == "__main__":
    main()