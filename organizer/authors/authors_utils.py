
#!/usr/bin/env python3
"""
authors_utils.py — Gemeinsame Utilities für Scrape, Analyse und Report.

Benötigt:
  - Python 3.9+
  - PyMuPDF (fitz)
  - pandas (für Aggregation/Export)
  - optional: pytesseract (+ Tesseract), pillow
  - optional: networkx

Environment:
  - FBNE_PROJECT_DIR (optional): Absoluter Projektpfad mit 'config/authors_report_template'.
    Fallback: heuristische Erkennung oder Default.
"""
from __future__ import annotations

import os
import re
import io
import json
import html
import hashlib
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional, Iterable, Set
from datetime import datetime

# ---------------------- Drittanbieter-Flags ----------------------
try:
    import fitz  # PyMuPDF
except Exception as e:
    raise RuntimeError("PyMuPDF (fitz) ist erforderlich. Installation: pip install pymupdf") from e

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

# Projekt-Defaults (werden durch detect_project_dir übersteuert)
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


@dataclass(frozen=True)
class WorkSpec:
    canonical: str
    kind: str                  # "work" | "series" | "generic"
    patterns: Tuple[str, ...]
    negatives: Tuple[str, ...]
    weight: float


@dataclass(frozen=True)
class WorkMention:
    pdf_file: str
    canonical: str
    kind: str
    page: int
    in_bib: int
    pattern: str
    context: str
    weight: float


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


# ---------------------- Text/Regex-Utils ----------------------
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

def compile_patterns(pattern_strs: Iterable[str]) -> List[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in pattern_strs]

def compile_bib_heading_patterns() -> List[re.Pattern]:
    return [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in BIB_HEADINGS]


# ---------------------- PDF & OCR ----------------------
def iter_pdfs(root: str) -> Iterable[str]:
    for d, _, files in os.walk(root):
        for fn in files:
            if fn.lower().endswith(".pdf"):
                yield os.path.join(d, fn)

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


# ---------------------- Autoren/Canon/Heuristik ----------------------
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
        # Musterstrings bauen
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


def load_authors_canon(path: str) -> Dict:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Autoren-Canon nicht gefunden: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if "authors" not in data or not isinstance(data["authors"], list):
        raise ValueError("autoren_canon.json: Feld 'authors' fehlt oder ist kein Array.")
    return data


def build_authors_from_canon(canon: Dict, heuristic_authors: Dict[str, Author]) -> Tuple[Dict[str, Author], Dict[str, dict], Dict[str, str]]:
    """
    Erzeugt:
      - authors: kanonische Autoren (ID->Author)
      - aux: Meta je Autor (negatives, filename_aliases, text_aliases)
      - merge_map: Weiterleitungen (canonical_id -> merge_into)
    """
    authors: Dict[str, Author] = {}
    aux: Dict[str, dict] = {}
    merge_map: Dict[str, str] = {}

    def _tokens(cid: str) -> List[str]:
        return [x for x in cid.replace("_", "-").split("-") if x]

    def _surnames(tokens: List[str]) -> List[str]:
        out: List[str] = []
        if tokens:
            out.append(tokens[0])
            if tokens[-1] != tokens[0]:
                out.append(tokens[-1])
        return [s for s in out if len(s) > 1 and not s.isdigit()]

    # Merge-Zuordnung
    for a in canon["authors"]:
        cid = a.get("canonical_id") or ""
        mi = a.get("merge_into")
        if mi and mi != cid:
            merge_map[cid] = mi

    # Nur kanonische IDs erzeugen
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


# ---------------------- Werke-Canon ----------------------
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
    for w in work_json.get("works", []):
        canon = w.get("canonical") or ""
        aliases = tuple(w.get("aliases", []) or [])
        if not canon or not aliases:
            continue
        specs.append(WorkSpec(canonical=canon, kind="work", patterns=aliases,
                              negatives=tuple(w.get("negatives", []) or []),
                              weight=float(weights.get("work", 1.0))))
    for s in work_json.get("series", []):
        canon = s.get("canonical") or ""
        aliases = tuple(s.get("aliases", []) or [])
        if not canon or not aliases:
            continue
        specs.append(WorkSpec(canonical=canon, kind="series", patterns=aliases,
                              negatives=tuple(s.get("negatives", []) or []),
                              weight=float(weights.get("series", 1.0))))
    for g in work_json.get("generic_terms", []):
        canon = g.get("label") or ""
        aliases = tuple(g.get("patterns", []) or [])
        if not canon or not aliases:
            continue
        specs.append(WorkSpec(canonical=canon, kind="generic", patterns=aliases,
                              negatives=tuple(g.get("negatives", []) or []),
                              weight=float(weights.get("generic", 1.2))))
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

    def lab(aid: str) -> str:
        a = authors.get(aid)
        return a.display if a else aid

    if not df_out.empty:
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

    count_authors = len(authors)
    count_edges = int(df_out.shape[0])

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


if __name__ == "__main__":
    def main() -> None:
        log_info("Selftest gestartet.")
        environment_report()
        # Kein automatischer Report-Run; Utilities werden von Sub-Skripten genutzt.
        log_info("Selftest Ende. Dieses Modul wird importiert.")
    main()

