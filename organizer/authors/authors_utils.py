"""
authors_utils.py — Utility-Funktionen und Modelle für Autor↔Autor-Referenznetz.

Usage:
  - Wird von authors_search.py und authors_gui.py importiert.
  - Nicht direkt ausführen. Ein kleines __main__ dient nur zum Selbsttest.

Benötigt:
  - Python 3.9+
  - PyMuPDF (fitz)
  - pandas (für Aggregation/Export)
  - optional: pytesseract (+ Tesseract), pillow
  - optional: networkx

Environment:
  - FBNE_PROJECT_DIR (optional): Absoluter Projektpfad, der 'config/authors_report_template' enthält.
    Fallback: automatische Erkennung oder Default '/Users/programming/PycharmProjects/Find_Bibliography_NEw'.
"""
from __future__ import annotations

import os
import re
import io
import json
import html
import hashlib
import unicodedata
from dataclasses import dataclass, asdict
from typing import Dict, List, Tuple, Optional, Iterable, Set
from datetime import datetime

# Drittanbieter
try:
    import fitz  # PyMuPDF
except Exception as e:
    raise RuntimeError("PyMuPDF (fitz) ist erforderlich. Installation: pip install pymupdf") from e

# Flags erst lazy in Funktionen geprüft
try:
    import pandas as pd  # noqa
    HAVE_PANDAS = True
except Exception:
    HAVE_PANDAS = False

try:
    import pytesseract  # noqa
    HAVE_TESS = True
except Exception:
    HAVE_TESS = False

try:
    import networkx as nx  # noqa
    HAVE_NX = True
except Exception:
    HAVE_NX = False

# ---------------------- Konfiguration ----------------------
TEXT_MIN_LEN = 120                 # OCR-Fallback wenn Text kurz
SNIPPET_LEN = 140                  # Kontextlänge
YEAR_RE = r"(1[4-9]\d{2}|20\d{2})" # 1400–2099
BIB_HEADINGS = [
    r"^\s*literatur\s*$",
    r"^\s*bibliographi[ea]\s*$",
    r"^\s*literaturverzeichnis\s*$",
    r"^\s*references\s*$",
    r"^\s*bibliography\s*$",
    r"^\s*quellen\s*(und\s*literatur)?\s*$",
]
LOG_TS_FMT = "%H:%M:%S"

# Projekt-Defaults
PROJECT_DIR_DEFAULT = "/Users/programming/PycharmProjects/Find_Bibliography_NEw"
TEMPLATE_SUBDIR = os.path.join("config", "authors_report_template")
DATA_SUBDIR = os.path.join("data", "authors_data")

# ---------------------- Modelle ----------------------
@dataclass(frozen=True)
class Author:
    author_id: str
    display: str
    surnames: Tuple[str, ...]
    pattern_strs: Tuple[str, ...]
    folded_surnames: Tuple[str, ...]


@dataclass(frozen=True)
class Mention:
    src_id: str
    tgt_id: str
    pdf_path: str
    page: int
    in_bib: bool
    pattern: str
    context: str


# ---------------------- Logging ----------------------
def _ts() -> str:
    return datetime.now().strftime(LOG_TS_FMT)

def log_info(msg: str) -> None:
    print(f"[{_ts()}][INFO] {msg}", flush=True)

def log_warn(msg: str) -> None:
    print(f"[{_ts()}][WARN] {msg}", flush=True)

def log_error(msg: str) -> None:
    print(f"[{_ts()}][ERROR] {msg}", flush=True)


# ---------------------- Projektpfad-Erkennung ----------------------
def _looks_like_project(p: str) -> bool:
    return os.path.isdir(os.path.join(p, TEMPLATE_SUBDIR))

def detect_project_dir(hint: Optional[str] = None) -> str:
    """
    Ermittelt den Projektpfad, der das Template-Verzeichnis enthält.
    Reihenfolge: ENV(FBNE_PROJECT_DIR) -> heuristische Suche (hint, __file__) -> Default.
    """
    env = os.environ.get("FBNE_PROJECT_DIR")
    if env and _looks_like_project(env):
        log_info(f"Projektpfad aus ENV: {env}")
        return os.path.abspath(env)

    # Heuristik: von hint aus aufwärts
    def ascend(start: str) -> Optional[str]:
        cur = os.path.abspath(start)
        for _ in range(6):
            if _looks_like_project(cur):
                return cur
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
        return None

    if hint:
        cand = ascend(hint)
        if cand:
            log_info(f"Projektpfad erkannt (hint): {cand}")
            return cand

    # Heuristik: von diesem File aus aufwärts
    this_dir = os.path.dirname(os.path.abspath(__file__))
    cand = ascend(this_dir)
    if cand:
        log_info(f"Projektpfad erkannt (__file__): {cand}")
        return cand

    # Fallback
    log_warn(f"Projektpfad-Fallback genutzt: {PROJECT_DIR_DEFAULT}")
    return PROJECT_DIR_DEFAULT


def get_template_dir(project_dir: str) -> str:
    tpl = os.path.join(project_dir, TEMPLATE_SUBDIR)
    if not os.path.isdir(tpl):
        raise FileNotFoundError(f"Template-Verzeichnis fehlt: {tpl}")
    return tpl


# ---------------------- Utils ----------------------
def normspace(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()

def strip_diacritics(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))

def diacritic_class(ch: str) -> str:
    sets = {
        'a': "[aàáâäãåā]", 'A': "[AÀÁÂÄÃÅĀ]",
        'o': "[oòóôöõō]", 'O': "[OÒÓÔÖÕŌ]",
        'u': "[uùúûüū]", 'U': "[UÙÚÛÜŪ]",
        'e': "[eèéêëē]", 'E': "[EÈÉÊËĒ]",
        'i': "[iìíîïī]", 'I': "[IÌÍÎÏĪ]",
        'y': "[yýÿ]", 'Y': "[YÝŸ]",
        's': "[sß]", 'S': "[Sẞ]",
        'c': "[cçćč]", 'C': "[CÇĆČ]",
        'z': "[zžźż]", 'Z': "[ZŽŹŻ]",
        'n': "[nñńň]", 'N': "[NÑŃŇ]",
    }
    return sets.get(ch, re.escape(ch))

def make_diacritic_regex(token: str) -> str:
    token = token.replace(".", r"\.")
    out = "".join(diacritic_class(c) for c in token)
    out = out.replace("-", r"[-\s]+")
    return out

def iter_pdfs(root: str) -> Iterable[str]:
    for d, _, files in os.walk(root):
        for fn in files:
            if fn.lower().endswith(".pdf"):
                yield os.path.join(d, fn)

def _display_from_tokens(tokens: List[str]) -> str:
    if len(tokens) >= 2:
        last = tokens[-1].capitalize()
        firsts = " ".join(t.capitalize() for t in tokens[:-1] if len(t) > 1)
        return f"{last}, {firsts}" if firsts else last
    return tokens[0].capitalize() if tokens else "Unbekannt"

def extract_authors_from_filenames(pdf_paths: List[str]) -> Dict[str, Author]:
    authors: Dict[str, Author] = {}
    for p in pdf_paths:
        base = os.path.splitext(os.path.basename(p))[0]
        left = base.split("__")[0].replace("_", "-").strip("-")
        if not left:
            continue
        tokens = [t for t in left.split("-") if t]
        display = _display_from_tokens(tokens)
        # Nachnamenkandidaten
        surs: List[str] = []
        if tokens:
            surs.append(tokens[0])
            if tokens[-1] != tokens[0]:
                surs.append(tokens[-1])
        surs = [s for s in surs if len(s) > 1 and not s.isdigit()]
        if not surs:
            continue
        # Musterstrings bauen (keine kompilierten Regex für Pickle-Freundlichkeit)
        pattern_strs: List[str] = []
        for sur in dict.fromkeys(surs):
            srx = make_diacritic_regex(sur)
            pat1 = rf"\b{srx}\b\s*,\s*[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\.\-]{{1,}}"
            pat2 = rf"\b[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\.\-]{{1,}}\s+{srx}\b"
            pat3 = rf"\b{srx}\b[^\n]{{0,30}}\b{YEAR_RE}\b"
            pattern_strs.extend([pat1, pat2, pat3])
        folded = tuple(strip_diacritics(s).casefold() for s in dict.fromkeys(surs))
        authors[left] = Author(
            author_id=left,
            display=display,
            surnames=tuple(dict.fromkeys(surs)),
            pattern_strs=tuple(pattern_strs),
            folded_surnames=folded,
        )
    return authors

def compile_patterns(pattern_strs: Iterable[str]) -> List[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in pattern_strs]

def compile_bib_heading_patterns() -> List[re.Pattern]:
    return [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in BIB_HEADINGS]

def detect_bibliography_pages(doc: "fitz.Document") -> Set[int]:
    pats = compile_bib_heading_patterns()
    bib_pages: Set[int] = set()
    start_seen = False
    for i in range(doc.page_count):
        page = doc.load_page(i)
        txt = (page.get_text("text") or "").strip()
        if not start_seen:
            for pat in pats:
                if pat.search(txt):
                    start_seen = True
                    break
        if start_seen:
            bib_pages.add(i)
    return bib_pages

def extract_text_with_ocr(page: "fitz.Page") -> str:
    txt = page.get_text("text") or ""
    if len(txt) >= TEXT_MIN_LEN:
        return txt
    if not HAVE_TESS:
        return txt
    try:
        pix = page.get_pixmap(dpi=300)
        from PIL import Image  # pillow
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        ocr = pytesseract.image_to_string(img, lang="deu+eng")
        return ocr or txt
    except Exception:
        return txt

def classify_section(page_index: int, bib_pages: Set[int]) -> str:
    return "bib" if page_index in bib_pages else "text"

def quick_page_filter(text_folded: str, target_folded_surnames: Tuple[str, ...]) -> bool:
    # Sehr schneller Vorfilter ohne Regex, diakritikfrei und casefolded
    return any(sur in text_folded for sur in target_folded_surnames)

def find_author_mentions(text: str, compiled_patterns: List[re.Pattern]) -> List[Tuple[Tuple[int, int], str]]:
    hits: List[Tuple[Tuple[int, int], str]] = []
    for pat in compiled_patterns:
        for m in pat.finditer(text):
            hits.append((m.span(), pat.pattern))
    return hits

def make_context(text: str, span: Tuple[int, int], length: int = SNIPPET_LEN) -> str:
    s, e = span
    mid = (s + e) // 2
    start = max(0, mid - length // 2)
    end = min(len(text), start + length)
    return normspace(text[start:end])

# ---------------------- Aggregation & Output ----------------------
def aggregate_edges(authors: Dict[str, Author], mentions: List[Mention]):
    if not HAVE_PANDAS:
        raise RuntimeError("pandas ist erforderlich. Installation: pip install pandas")
    import pandas as pd  # local import

    nodes = [{"author_id": aid, "display": a.display, "surnames": "|".join(a.surnames)} for aid, a in authors.items()]
    df_nodes = pd.DataFrame(nodes)

    rows = [{
        "src": m.src_id,
        "tgt": m.tgt_id,
        "pdf_file": m.pdf_path,
        "page": m.page,
        "in_bib": int(m.in_bib),
        "pattern": m.pattern,
        "context": m.context
    } for m in mentions]
    df_raw = pd.DataFrame(rows)
    if df_raw.empty:
        df_edges = pd.DataFrame(columns=["src", "tgt", "total", "bib_hits", "text_hits", "examples", "weighted"])
        return df_nodes, df_edges

    agg = df_raw.groupby(["src", "tgt"]).agg(
        total=("pdf_file", "count"),
        bib_hits=("in_bib", "sum"),
        examples=("context", lambda s: " | ".join(s.head(3)))
    ).reset_index()
    agg["text_hits"] = agg["total"] - agg["bib_hits"]
    agg["weighted"] = agg["bib_hits"] * 2 + agg["text_hits"] * 1
    return df_nodes, agg

def ensure_session_dir(base: str, pdfs: List[str]) -> str:
    """
    Erstellt einen neuen Session-Ordner IM PROJEKT unter data/authors_data,
    unabhängig davon, wo die PDFs liegen.
    """
    project_dir = detect_project_dir(hint=base)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    h = hashlib.sha1(("|".join(sorted(os.path.basename(p) for p in pdfs))).encode("utf-8")).hexdigest()[:8]
    root = os.path.join(project_dir, DATA_SUBDIR)
    os.makedirs(root, exist_ok=True)
    session_dir = os.path.join(root, f"session_{ts}_{h}")
    os.makedirs(session_dir, exist_ok=True)
    log_info(f"Session-Root: {root}")
    return session_dir

def write_csvs(out_dir: str, df_nodes, df_edges) -> Tuple[str, str]:
    nodes_path = os.path.join(out_dir, "authors_nodes.csv")
    edges_path = os.path.join(out_dir, "authors_edges.csv")
    df_nodes.to_csv(nodes_path, index=False, sep=";")
    df_edges.to_csv(edges_path, index=False, sep=";")
    log_info(f"CSV geschrieben: {nodes_path}")
    log_info(f"CSV geschrieben: {edges_path}")
    return nodes_path, edges_path

def write_gexf(out_dir: str, authors: Dict[str, Author], df_edges) -> Optional[str]:
    if not HAVE_NX:
        log_info("networkx nicht verfügbar. GEXF wird übersprungen.")
        return None
    import networkx as nx  # local import
    G = nx.DiGraph()
    for aid, a in authors.items():
        G.add_node(aid, label=a.display)
    for _, r in df_edges.iterrows():
        G.add_edge(r["src"], r["tgt"], weight=float(r["weighted"]), total=int(r["total"]),
                   bib=int(r["bib_hits"]), text=int(r["text_hits"]))
    out = os.path.join(out_dir, "authors_network.gexf")
    nx.write_gexf(G, out)
    log_info(f"GEXF geschrieben: {out}")
    return out

def _compute_asset_rel(out_dir: str, template_dir: str) -> str:
    rel = os.path.relpath(template_dir, start=out_dir).replace(os.sep, "/")
    return rel

def _read_template_html(template_dir: str) -> str:
    path = os.path.join(template_dir, "authors_report.html")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Template-Datei fehlt: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def _esc(s: object) -> str:
    return html.escape("" if s is None else str(s), quote=True)

def write_html_report(out_dir: str, authors: Dict[str, Author], df_edges) -> str:
    """
    Rendert den Report mit dem HTML/CSS/JS-Template aus config/authors_report_template.
    Ersetzt Platzhalter und speichert authors_report.html im Session-Ordner.
    """
    import pandas as pd  # type: ignore

    project_dir = detect_project_dir(hint=out_dir)
    template_dir = get_template_dir(project_dir)
    tpl_html = _read_template_html(template_dir)
    asset_rel = _compute_asset_rel(out_dir, template_dir)

    df_out = df_edges.copy()
    # Labels anreichern
    def lab(aid: str) -> str:
        a = authors.get(aid)
        return a.display if a else aid

    df_out["src_label"] = df_out["src"].map(lab)
    df_out["tgt_label"] = df_out["tgt"].map(lab)

    # Top-Listen
    if df_out.empty:
        top_citers = pd.Series(dtype=float)
        top_cited = pd.Series(dtype=float)
    else:
        top_citers = df_out.groupby("src_label")["weighted"].sum().sort_values(ascending=False).head(10)
        top_cited  = df_out.groupby("tgt_label")["weighted"].sum().sort_values(ascending=False).head(10)

    def rows_from_series(ser: pd.Series) -> str:
        parts: List[str] = []
        for name, val in ser.items():
            parts.append(f"<tr><td>{_esc(name)}</td><td>{int(val)}</td></tr>")
        return "\n".join(parts)

    def rows_from_edges(dfv: pd.DataFrame) -> str:
        parts: List[str] = []
        for _, r in dfv.sort_values("weighted", ascending=False).iterrows():
            parts.append(
                "<tr>"
                f"<td>{_esc(r.get('src_label',''))}</td>"
                f"<td>{_esc(r.get('tgt_label',''))}</td>"
                f"<td>{int(r.get('total',0))}</td>"
                f"<td>{int(r.get('bib_hits',0))}</td>"
                f"<td>{int(r.get('text_hits',0))}</td>"
                f"<td>{_esc(str(r.get('examples','')))}</td>"
                "</tr>"
            )
        return "\n".join(parts)

    top_citers_rows = rows_from_series(top_citers)
    top_cited_rows = rows_from_series(top_cited)
    edges_rows = rows_from_edges(df_out)

    # Autorenanzahl aus dict, nicht aus df_edges (robuster)
    count_authors = len(authors)
    count_edges = int(df_out.shape[0])

    # Platzhalter ersetzen
    html_str = tpl_html
    html_str = html_str.replace("{{ASSET_REL}}", _esc(asset_rel))
    html_str = html_str.replace("{{COUNT_AUTHORS}}", str(count_authors))
    html_str = html_str.replace("{{COUNT_EDGES}}", str(count_edges))
    html_str = html_str.replace("{{TOP_CITERS_ROWS}}", top_citers_rows)
    html_str = html_str.replace("{{TOP_CITED_ROWS}}", top_cited_rows)
    html_str = html_str.replace("{{EDGES_ROWS}}", edges_rows)

    out = os.path.join(out_dir, "authors_report.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html_str)
    log_info(f"HTML geschrieben (Template): {out}")
    log_info(f"Assets relativ verlinkt: {asset_rel}/(style.css|script.js)")
    return out

def write_session_meta(out_dir: str, base: str, pdfs: List[str]) -> str:
    meta = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "base_folder": os.path.abspath(base),
        "num_pdfs": len(pdfs),
        "pdfs": [os.path.relpath(p, base) for p in pdfs],
        "env": {
            "pymupdf": True,
            "pandas": HAVE_PANDAS,
            "pytesseract": HAVE_TESS,
            "networkx": HAVE_NX,
            "ocr_threshold_chars": TEXT_MIN_LEN
        }
    }
    out = os.path.join(out_dir, "session_meta.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    log_info(f"Session-Metadaten geschrieben: {out}")
    return out

def environment_report() -> None:
    log_info("Bibliotheken:")
    print("  PyMuPDF: OK")
    print(f"  pandas: {'OK' if HAVE_PANDAS else 'NEIN'}")
    print(f"  pytesseract: {'OK' if HAVE_TESS else 'NEIN'}")
    print(f"  networkx: {'OK' if HAVE_NX else 'NEIN'}")
    if HAVE_TESS:
        try:
            ver = pytesseract.get_tesseract_version()
            print(f"  Tesseract-Version: {ver}")
        except Exception:
            pass
    log_info(f"OCR-Fallback aktiv bei < {TEXT_MIN_LEN} Zeichen.")


# ---------------------- Optionaler Selbsttest ---------------------------------
def _selftest_render_if_csv_exists() -> None:
    """
    Kleiner Smoke-Test: Falls im neuesten Session-Ordner CSVs liegen,
    rendere Report mit Template erneut.
    """
    if not HAVE_PANDAS:
        log_warn("pandas nicht installiert, Selftest übersprungen.")
        return
    import pandas as pd  # noqa
    project_dir = detect_project_dir()
    data_dir = os.path.join(project_dir, DATA_SUBDIR)
    if not os.path.isdir(data_dir):
        log_warn(f"Kein Datenverzeichnis: {data_dir}")
        return
    # Neueste Session finden
    cands = []
    for name in os.listdir(data_dir):
        p = os.path.join(data_dir, name)
        if os.path.isdir(p) and name.startswith("session_"):
            nodes = os.path.join(p, "authors_nodes.csv")
            edges = os.path.join(p, "authors_edges.csv")
            if os.path.isfile(nodes) and os.path.isfile(edges):
                cands.append((os.path.getmtime(p), p))
    if not cands:
        log_warn("Keine Session mit CSVs gefunden. Selftest beendet.")
        return
    cands.sort(key=lambda t: t[0], reverse=True)
    session_dir = cands[0][1]
    log_info(f"Selftest-Session: {session_dir}")
    df_nodes = pd.read_csv(os.path.join(session_dir, "authors_nodes.csv"), sep=";")
    df_edges = pd.read_csv(os.path.join(session_dir, "authors_edges.csv"), sep=";")
    # Autoren-Map für Labels
    authors: Dict[str, Author] = {}
    for aid, disp in zip(df_nodes["author_id"].astype(str), df_nodes["display"].astype(str)):
        authors[aid] = Author(aid, disp, tuple(), tuple(), tuple())
    write_html_report(session_dir, authors, df_edges)


# Nur für manuellen Schnelltest; im regulären Betrieb wird dieses Modul importiert.
if __name__ == "__main__":
    def main() -> None:
        log_info("Selftest gestartet.")
        environment_report()
        try:
            _selftest_render_if_csv_exists()
        except Exception as e:
            log_error(f"Selftest-Fehler: {e}")
        log_info("Selftest Ende. Usage: Modul importieren und Funktionen aufrufen.")
    main()