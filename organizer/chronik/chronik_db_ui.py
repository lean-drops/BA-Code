#!/usr/bin/env python3
"""
PySide6-GUI für die Verwaltung der Chroniken-DB.
- Zeigt Tabellen 'chroniken' und 'works_json'
- Scannt PDFs im Ordner und markiert Verfügbarkeit
- Registriert neue PDFs (Kopieren + Verknüpfen)
- Bearbeitet Alias-Regex (works_json.aliases_json)
- Optional: schlägt Aliases via OpenAI vor (OPENAI_API_KEY in .env)

Voraussetzungen:
- PySide6, sqlite3, Python 3.9+
- DB erwartet unter: /Users/programming/PycharmProjects/Find_Bibliography_NEw/config/chroniken.sqlite3
- PDF-Ordner:       /Users/programming/PycharmProjects/Find_Bibliography_NEw/data/chroniken_library/pdf
- .env mit OPENAI_API_KEY unter: /Users/programming/PycharmProjects/Find_Bibliography_NEw/.env

Usage:
- Datei starten. Debug-Logs erscheinen in der Konsole.
"""

from __future__ import annotations
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Iterable, Set

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QSize
from PySide6.QtGui import QAction, QIcon
from PySide6.QtSql import QSqlDatabase, QSqlTableModel, QSqlQuery
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QMessageBox, QFileDialog, QWidget, QVBoxLayout,
    QHBoxLayout, QPushButton, QLabel, QLineEdit, QTabWidget, QTableView,
    QGroupBox, QFormLayout, QCheckBox, QTextEdit, QSplitter, QComboBox
)

# ------------------------ Feste Pfade ------------------------

PROJECT = Path("/Users/programming/PycharmProjects/Find_Bibliography_NEw")
CONFIG_DIR = PROJECT / "config"
DB_PATH = CONFIG_DIR / "chroniken.sqlite3"
PDF_DIR = PROJECT / "data" / "chroniken_library" / "pdf"
ENV_PATH = PROJECT / ".env"

# ------------------------ Utility ------------------------

def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r"\s+", " ", s)
    return s.strip()

def canonical_key(s: str) -> str:
    base = normalize_text(s)
    base = re.sub(r"[^a-z0-9]+", " ", base)
    base = re.sub(r"\b(chronik|chronicon|schweizerchronik|berner|zuercher|zürcher|luzerner|eidgenossenschaft|helveticum|helvetica)\b", "", base)
    base = re.sub(r"\s+", " ", base).strip()
    return base

def tokenize(s: str) -> Set[str]:
    s = canonical_key(s)
    return set(t for t in s.split() if len(t) >= 3)

def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    A, B = set(a), set(b)
    if not A and not B:
        return 1.0
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)

def read_env(path: Path) -> Dict[str, str]:
    data: Dict[str, str] = {}
    if not path.exists():
        return data
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip().strip('"').strip("'")
    return data

def write_env(path: Path, data: Dict[str, str]) -> None:
    lines = [f"{k}={json.dumps(v)[1:-1]}" for k, v in data.items()]
    with path.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

def open_file_with_os(path: Path) -> None:
    if sys.platform.startswith("darwin"):
        subprocess.run(["open", str(path)], check=False)
    elif os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", str(path)], check=False)

def ensure_dirs() -> None:
    if not PDF_DIR.exists():
        print(f"[DEBUG] Erzeuge PDF-Verzeichnis: {PDF_DIR}")
        PDF_DIR.mkdir(parents=True, exist_ok=True)

def compile_aliases(patterns: List[str]) -> List[re.Pattern]:
    out = []
    for p in patterns:
        try:
            out.append(re.compile(p, re.IGNORECASE))
        except re.error:
            out.append(re.compile(re.escape(p), re.IGNORECASE))
    return out

# ------------------------ OpenAI Integration ------------------------

def suggest_aliases(api_key: str, title: str, existing_aliases: List[str], sample_filenames: List[str]) -> List[str]:
    """
    Liefert Alias-/Regex-Vorschläge. Nutzt openai wenn verfügbar.
    Gibt leere Liste zurück, wenn kein Key oder Paket.
    """
    print("[DEBUG] OpenAI Alias-Vorschlag gestartet")
    if not api_key:
        print("[DEBUG] Kein OPENAI_API_KEY gefunden")
        return []
    try:
        import openai  # type: ignore
    except Exception as e:
        print(f"[DEBUG] openai-Paket fehlt/Fehler: {e}")
        return []
    try:
        # Kompatibel zu älterem API
        openai.api_key = api_key
        sys_prompt = (
            "Du erzeugst kurze Regex-Aliases für historische Werke. "
            "Gib nur eine JSON-Liste von 3-6 kompakten Regex-Strings zurück. "
            "Kein Text, nur JSON-Liste. Verwende keine Anker, vermeide Überfitting."
        )
        user_prompt = {
            "title": title,
            "existing_aliases": existing_aliases,
            "pdf_names_sample": sample_filenames[:25]
        }
        msg = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False)}
        ]
        # Modellwahl defensiv
        model = "gpt-4o-mini"
        resp = openai.ChatCompletion.create(model=model, messages=msg, temperature=0.2, n=1, max_tokens=200)
        text = resp["choices"][0]["message"]["content"]
        print(f"[DEBUG] OpenAI Antwort: {text[:200]}...")
        try:
            data = json.loads(text)
            if isinstance(data, list):
                # Einfaches Sanitizing
                cleaned = []
                for x in data:
                    if isinstance(x, str) and 1 <= len(x) <= 120:
                        cleaned.append(x)
                return cleaned[:8]
        except Exception:
            # Fallback: naive Extraktion von Zeilen
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            return lines[:5]
    except Exception as e:
        print(f"[DEBUG] OpenAI-Fehler: {e}")
    return []

# ------------------------ DB-Helfer ------------------------

def connect_qt_db(db_path: Path) -> QSqlDatabase:
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite nicht gefunden: {db_path}")
    db = QSqlDatabase.addDatabase("QSQLITE")
    db.setDatabaseName(str(db_path))
    if not db.open():
        raise RuntimeError("Konnte SQLite nicht öffnen.")
    return db

def exec_sql(db: QSqlDatabase, sql: str, params: Tuple = ()) -> None:
    q = QSqlQuery(db)
    q.prepare(sql)
    for i, p in enumerate(params):
        q.bindValue(i, p)
    if not q.exec():
        raise RuntimeError(f"SQL-Fehler: {q.lastError().text()}")

def fetchall(db: QSqlDatabase, sql: str, params: Tuple = ()) -> List[Tuple]:
    q = QSqlQuery(db)
    q.prepare(sql)
    for i, p in enumerate(params):
        q.bindValue(i, p)
    if not q.exec():
        raise RuntimeError(f"SQL-Fehler: {q.lastError().text()}")
    out = []
    while q.next():
        row = []
        for c in range(q.record().count()):
            row.append(q.value(c))
        out.append(tuple(row))
    return out

def list_pdfs() -> List[str]:
    ensure_dirs()
    return [p.name for p in PDF_DIR.glob("*.pdf")]

# ------------------------ GUI ------------------------

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Chroniken Manager")
        self.resize(1200, 800)

        # ENV
        self.env = read_env(ENV_PATH)
        self.api_key = self.env.get("OPENAI_API_KEY", "")

        # DB
        try:
            self.db = connect_qt_db(DB_PATH)
        except Exception as e:
            QMessageBox.critical(self, "DB-Fehler", str(e))
            sys.exit(2)

        # Modelle
        self.model_chroniken = QSqlTableModel(self, self.db)
        self.model_chroniken.setTable("chroniken")
        self.model_chroniken.setEditStrategy(QSqlTableModel.OnFieldChange)
        self.model_chroniken.select()

        self.model_works = QSqlTableModel(self, self.db)
        self.model_works.setTable("works_json")
        self.model_works.setEditStrategy(QSqlTableModel.OnFieldChange)
        self.model_works.select()

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.tabs.addTab(self._build_overview_tab(), "Übersicht")
        self.tabs.addTab(self._build_chroniken_tab(), "Chroniken")
        self.tabs.addTab(self._build_works_tab(), "Works/JSON")
        self.tabs.addTab(self._build_settings_tab(), "Einstellungen")

        self._build_menu()
        self.refresh_overview()
        print("[DEBUG] GUI bereit")

    # ----- Menü -----
    def _build_menu(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("Datei")

        act_rescan = QAction("PDFs scannen", self)
        act_rescan.triggered.connect(self.scan_pdfs_and_update)
        file_menu.addAction(act_rescan)

        act_add = QAction("PDF registrieren…", self)
        act_add.triggered.connect(self.register_new_pdf)
        file_menu.addAction(act_add)

        act_exit = QAction("Beenden", self)
        act_exit.triggered.connect(self.close)
        file_menu.addAction(act_exit)

    # ----- Übersicht -----
    def _build_overview_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        self.lbl_counts = QLabel("…")
        v.addWidget(self.lbl_counts)

        h = QHBoxLayout()
        self.btn_refresh = QPushButton("Aktualisieren")
        self.btn_refresh.clicked.connect(self.refresh_overview)
        h.addWidget(self.btn_refresh)

        self.btn_list_missing = QPushButton("Fehlende PDFs auflisten")
        self.btn_list_missing.clicked.connect(self.show_missing_dialog)
        h.addWidget(self.btn_list_missing)

        h.addStretch(1)
        v.addLayout(h)
        return w

    def refresh_overview(self) -> None:
        rows = fetchall(self.db, "SELECT COUNT(*), SUM(pdf_present) FROM chroniken;")
        total = rows[0][0] or 0
        present = rows[0][1] or 0
        missing = total - present
        self.lbl_counts.setText(f"Chroniken gesamt: {total} | PDFs vorhanden: {present} | Fehlend: {missing}")

    def show_missing_dialog(self) -> None:
        miss = fetchall(self.db, 'SELECT id, COALESCE("canonical_key",""), COALESCE("pdf_filename","") FROM chroniken WHERE pdf_present=0 ORDER BY id;')
        text = "\n".join([f"{r[0]}: {r[1]}" for r in miss]) or "Keine Lücken."
        QMessageBox.information(self, "Fehlende PDFs", text)

    # ----- Chroniken -----
    def _build_chroniken_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        # Filterleiste
        fl = QHBoxLayout()
        fl.addWidget(QLabel("Suche:"))
        self.edt_search = QLineEdit()
        self.edt_search.setPlaceholderText("Text in canonical_key …")
        self.edt_search.textChanged.connect(self.apply_filter)
        fl.addWidget(self.edt_search)

        self.cb_only_missing = QCheckBox("nur fehlende PDFs")
        self.cb_only_missing.stateChanged.connect(self.apply_filter)
        fl.addWidget(self.cb_only_missing)
        fl.addStretch(1)
        v.addLayout(fl)

        # Tabelle
        self.view_chroniken = QTableView()
        self.view_chroniken.setModel(self.model_chroniken)
        self.view_chroniken.setSelectionBehavior(QTableView.SelectRows)
        self.view_chroniken.setSortingEnabled(True)
        self.view_chroniken.resizeColumnsToContents()
        v.addWidget(self.view_chroniken)

        # Aktionen
        hl = QHBoxLayout()
        btn_rescan = QPushButton("PDFs scannen")
        btn_rescan.clicked.connect(self.scan_pdfs_and_update)
        hl.addWidget(btn_rescan)

        btn_register = QPushButton("PDF registrieren…")
        btn_register.clicked.connect(self.register_new_pdf)
        hl.addWidget(btn_register)

        btn_open = QPushButton("PDF öffnen")
        btn_open.clicked.connect(self.open_selected_pdf)
        hl.addWidget(btn_open)

        btn_rematch = QPushButton("Re-Match Aliases → chroniken")
        btn_rematch.clicked.connect(self.rebind_works_to_chroniken)
        hl.addWidget(btn_rematch)

        hl.addStretch(1)
        v.addLayout(hl)
        return w

    def apply_filter(self) -> None:
        parts = []
        q = self.edt_search.text().strip().replace("'", "''")
        if q:
            parts.append(f"canonical_key LIKE '%{q}%'")
        if self.cb_only_missing.isChecked():
            parts.append("pdf_present=0")
        filt = " AND ".join(parts) if parts else ""
        print(f"[DEBUG] Filter: {filt}")
        self.model_chroniken.setFilter(filt)
        self.model_chroniken.select()

    def open_selected_pdf(self) -> None:
        idx = self.view_chroniken.currentIndex()
        if not idx.isValid():
            QMessageBox.warning(self, "Hinweis", "Keine Zeile gewählt.")
            return
        row = idx.row()
        fn = self.model_chroniken.index(row, self._col_index(self.model_chroniken, "pdf_filename")).data()
        if not fn:
            QMessageBox.information(self, "Hinweis", "Kein PDF verknüpft.")
            return
        path = PDF_DIR / fn
        if not path.exists():
            QMessageBox.warning(self, "Fehler", f"Datei fehlt: {path}")
            return
        open_file_with_os(path)

    # ----- Works/JSON -----
    def _build_works_tab(self) -> QWidget:
        w = QWidget()
        split = QSplitter(Qt.Horizontal)

        left = QWidget()
        lv = QVBoxLayout(left)
        self.view_works = QTableView()
        self.view_works.setModel(self.model_works)
        self.view_works.setSelectionBehavior(QTableView.SelectRows)
        self.view_works.setSortingEnabled(True)
        self.view_works.selectionModel().currentRowChanged.connect(self.load_aliases_for_selected)
        lv.addWidget(self.view_works)

        hl = QHBoxLayout()
        btn_bind = QPushButton("Re-Match → chroniken")
        btn_bind.clicked.connect(self.rebind_works_to_chroniken)
        hl.addWidget(btn_bind)

        btn_save_alias = QPushButton("Aliases speichern")
        btn_save_alias.clicked.connect(self.save_aliases_from_editor)
        hl.addWidget(btn_save_alias)

        btn_suggest = QPushButton("Aliasvorschläge (OpenAI)")
        btn_suggest.clicked.connect(self.suggest_aliases_for_selected)
        hl.addWidget(btn_suggest)

        hl.addStretch(1)
        lv.addLayout(hl)

        right = QWidget()
        rv = QVBoxLayout(right)
        self.lbl_work_title = QLabel("json_canonical: ")
        rv.addWidget(self.lbl_work_title)
        self.txt_aliases = QTextEdit()
        self.txt_aliases.setPlaceholderText('JSON-Liste, z. B. ["tschudi", "chronicon helveticum", "glarus.*tschudi"]')
        rv.addWidget(self.txt_aliases)

        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([700, 500])

        outer = QVBoxLayout(w)
        outer.addWidget(split)
        return w

    def load_aliases_for_selected(self, curr: QModelIndex, prev: QModelIndex) -> None:
        if not curr.isValid():
            return
        row = curr.row()
        title = self.model_works.index(row, self._col_index(self.model_works, "json_canonical")).data() or ""
        aliases = self.model_works.index(row, self._col_index(self.model_works, "aliases_json")).data() or "[]"
        self.lbl_work_title.setText(f"json_canonical: {title}")
        self.txt_aliases.setPlainText(aliases)

    def save_aliases_from_editor(self) -> None:
        idx = self.view_works.currentIndex()
        if not idx.isValid():
            QMessageBox.warning(self, "Hinweis", "Kein Eintrag gewählt.")
            return
        row = idx.row()
        try:
            data = json.loads(self.txt_aliases.toPlainText())
            if not isinstance(data, list):
                raise ValueError("Aliases müssen eine JSON-Liste sein.")
        except Exception as e:
            QMessageBox.critical(self, "JSON-Fehler", str(e))
            return
        self.model_works.setData(self.model_works.index(row, self._col_index(self.model_works, "aliases_json")), json.dumps(data, ensure_ascii=False))
        if not self.model_works.submitAll():
            QMessageBox.critical(self, "Fehler", "Konnte Aliases nicht speichern.")
            return
        QMessageBox.information(self, "OK", "Aliases gespeichert.")

    def suggest_aliases_for_selected(self) -> None:
        idx = self.view_works.currentIndex()
        if not idx.isValid():
            QMessageBox.warning(self, "Hinweis", "Kein Eintrag gewählt.")
            return
        row = idx.row()
        title = self.model_works.index(row, self._col_index(self.model_works, "json_canonical")).data() or ""
        aliases_raw = self.model_works.index(row, self._col_index(self.model_works, "aliases_json")).data() or "[]"
        try:
            aliases = json.loads(aliases_raw)
            if not isinstance(aliases, list):
                aliases = []
        except Exception:
            aliases = []

        pdf_names = list_pdfs()
        print(f"[DEBUG] Hole OpenAI-Aliasvorschläge für: {title}")
        suggestions = suggest_aliases(self.api_key, title, aliases, pdf_names)
        if not suggestions:
            QMessageBox.information(self, "Hinweis", "Keine Vorschläge (API-Key/Package prüfen).")
            return
        merged = list(dict.fromkeys(aliases + suggestions))
        self.txt_aliases.setPlainText(json.dumps(merged, ensure_ascii=False, indent=2))
        QMessageBox.information(self, "Vorschläge", f"{len(suggestions)} Vorschläge eingefügt (noch speichern).")

    # ----- Einstellungen -----
    def _build_settings_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        grp = QGroupBox(".env / Schlüssel")
        form = QFormLayout(grp)
        self.lbl_env_path = QLabel(str(ENV_PATH))
        form.addRow("Pfad .env:", self.lbl_env_path)

        mask = self._mask_key(self.api_key)
        self.edt_api_key = QLineEdit(mask)
        self.edt_api_key.setEchoMode(QLineEdit.Password)
        form.addRow("OPENAI_API_KEY:", self.edt_api_key)

        btns = QHBoxLayout()
        btn_save = QPushButton("Speichern")
        btn_save.clicked.connect(self.save_env_key)
        btn_reload = QPushButton("Neu laden")
        btn_reload.clicked.connect(self.reload_env)
        btns.addWidget(btn_save)
        btns.addWidget(btn_reload)
        form.addRow(btns)

        v.addWidget(grp)
        v.addStretch(1)
        return w

    def _mask_key(self, key: str) -> str:
        if not key:
            return ""
        return key[:4] + "…" + key[-4:] if len(key) > 8 else "…" * 6

    def save_env_key(self) -> None:
        val = self.edt_api_key.text().strip()
        if val and "…" in val:
            QMessageBox.information(self, "Hinweis", "Maske erkannt. Trage vollständigen Key ein, um zu ändern.")
            return
        env = read_env(ENV_PATH)
        if val:
            env["OPENAI_API_KEY"] = val
        else:
            env.pop("OPENAI_API_KEY", None)
        write_env(ENV_PATH, env)
        self.env = env
        self.api_key = env.get("OPENAI_API_KEY", "")
        QMessageBox.information(self, "OK", "Gespeichert.")

    def reload_env(self) -> None:
        self.env = read_env(ENV_PATH)
        self.api_key = self.env.get("OPENAI_API_KEY", "")
        self.edt_api_key.setText(self._mask_key(self.api_key))
        QMessageBox.information(self, "OK", "Neu geladen.")

    # ----- Aktionen -----

    def rebind_works_to_chroniken(self) -> None:
        print("[DEBUG] Re-Match works_json → chroniken")
        # Map chroniken: id -> tokens + key
        rows = fetchall(self.db, 'SELECT id, COALESCE(canonical_key,""), COALESCE("werk", COALESCE("titel","")) FROM chroniken;')
        c_map: Dict[str, Tuple[int, Set[str]]] = {}
        id_by_key: Dict[str, int] = {}
        for cid, ckey, title in rows:
            toks = tokenize((title or "") + " " + (ckey or ""))
            c_map[ckey] = (cid, toks)
            id_by_key[ckey] = cid

        works = fetchall(self.db, 'SELECT json_canonical, canonical_key FROM works_json;')
        for jc, ck in works:
            if ck in id_by_key:
                exec_sql(self.db, "UPDATE works_json SET matched_chronik_id=?, match_confidence=? WHERE json_canonical=?;",
                         (id_by_key[ck], 1.0, jc))
            else:
                # Jaccard über alle
                jw_toks = tokenize(jc or "")
                best_id = None
                best = 0.0
                for ckey, (cid, toks) in c_map.items():
                    score = jaccard(jw_toks, toks)
                    if score > best:
                        best = score
                        best_id = cid
                exec_sql(self.db, "UPDATE works_json SET matched_chronik_id=?, match_confidence=? WHERE json_canonical=?;",
                         (best_id, round(best, 4), jc))
        self.model_works.select()
        QMessageBox.information(self, "OK", "Re-Match abgeschlossen.")

    def scan_pdfs_and_update(self) -> None:
        print("[DEBUG] Starte PDF-Scan")
        ensure_dirs()
        pdfs = list_pdfs()
        pdf_norm = [(nm, normalize_text(nm)) for nm in pdfs]

        # Aliases je canonical_key
        alias_dict: Dict[str, List[re.Pattern]] = {}
        rows = fetchall(self.db, "SELECT canonical_key, aliases_json FROM works_json;")
        for ckey, aj in rows:
            pats: List[str] = []
            try:
                data = json.loads(aj or "[]")
                if isinstance(data, list):
                    pats = [str(x) for x in data]
            except Exception:
                pass
            alias_dict[ckey or ""] = compile_aliases(pats)

        # Alle chroniken
        chron_rows = fetchall(self.db, 'SELECT id, COALESCE("canonical_key",""), COALESCE("werk", COALESCE("titel","")) FROM chroniken;')
        updated = 0
        for cid, ckey, title in chron_rows:
            matched = None
            # Regex-Match
            regs = alias_dict.get(ckey, [])
            if regs:
                for nm, nm_norm in pdf_norm:
                    if any(r.search(nm) or r.search(nm_norm) for r in regs):
                        matched = nm
                        break
            # Fallback: Text-Tokens
            if not matched:
                toks = [t for t in tokenize((title or "") + " " + (ckey or "")) if len(t) >= 4]
                for nm, nm_norm in pdf_norm:
                    if toks and all(t in nm_norm for t in toks[:2]):
                        matched = nm
                        break
            if matched:
                exec_sql(self.db, "UPDATE chroniken SET pdf_present=1, pdf_filename=? WHERE id=?;", (matched, cid))
                updated += 1
        self.model_chroniken.select()
        self.refresh_overview()
        QMessageBox.information(self, "Scan", f"Aktualisiert: {updated}")

    def register_new_pdf(self) -> None:
        print("[DEBUG] PDF registrieren…")
        ensure_dirs()
        fn, _ = QFileDialog.getOpenFileName(self, "PDF wählen", str(Path.home()), "PDF-Dateien (*.pdf)")
        if not fn:
            return
        src = Path(fn)
        if src.suffix.lower() != ".pdf":
            QMessageBox.warning(self, "Fehler", "Nur PDF erlaubt.")
            return
        # Zielname unik
        dst = PDF_DIR / src.name
        i = 1
        while dst.exists():
            dst = PDF_DIR / f"{src.stem}_{i}.pdf"
            i += 1
        shutil.copy2(src, dst)
        print(f"[DEBUG] Kopiert nach: {dst}")

        # Versuche: aktuelle Auswahl zu verknüpfen
        cid = self._selected_chronik_id()
        if cid:
            exec_sql(self.db, "UPDATE chroniken SET pdf_present=1, pdf_filename=? WHERE id=?;", (dst.name, cid))
            self.model_chroniken.select()
            self.refresh_overview()
            QMessageBox.information(self, "OK", f"Verknüpft mit ID {cid}.")
            return

        # Sonst: best match über Tokens
        rows = fetchall(self.db, 'SELECT id, COALESCE("canonical_key",""), COALESCE("werk", COALESCE("titel","")) FROM chroniken;')
        nm_norm = normalize_text(dst.name)
        best_id = None
        best = 0.0
        for cid2, ckey, title in rows:
            score = jaccard(set(nm_norm.split("_")), tokenize((title or "") + " " + (ckey or "")))
            if score > best:
                best = score
                best_id = cid2
        if best_id:
            exec_sql(self.db, "UPDATE chroniken SET pdf_present=1, pdf_filename=? WHERE id=?;", (dst.name, best_id))
            self.model_chroniken.select()
            self.refresh_overview()
            QMessageBox.information(self, "OK", f"Auto-verknüpft mit ID {best_id} (Score {best:.2f}).")
        else:
            QMessageBox.information(self, "Hinweis", "PDF kopiert, aber nicht verknüpft. Bitte manuell zuordnen.")

    # ----- Helpers -----

    def _selected_chronik_id(self) -> Optional[int]:
        idx = self.view_chroniken.currentIndex()
        if not idx.isValid():
            return None
        row = idx.row()
        id_col = self._col_index(self.model_chroniken, "id")
        return self.model_chroniken.index(row, id_col).data()

    def _col_index(self, model: QSqlTableModel, name: str) -> int:
        for c in range(model.columnCount()):
            if model.headerData(c, Qt.Horizontal) == name:
                return c
        # Fallback: try to fetch real field names from record
        rec = model.record()
        for c in range(rec.count()):
            if rec.fieldName(c) == name:
                return c
        # Worst-case
        return 0

# ------------------------ main ------------------------

def main() -> None:
    print("[DEBUG] Starte GUI")
    ensure_dirs()
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()