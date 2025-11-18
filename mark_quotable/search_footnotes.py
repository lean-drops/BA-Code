# /Users/programming/PycharmProjects/Find_Bibliography_NEw/citation_gui_zotero.py
"""
GUI zum Bearbeiten von citations_template.json mit optionaler Zotero-Anbindung.

Dependencies:
    pip install python-docx pyzotero

Nutzung:
    1. Pfade und Zotero-Konfiguration unten anpassen.
    2. python citation_gui_zotero.py
    3. Links Eintrag auswählen, rechts Fussnote/Zotero-Key bearbeiten, oben auf "Speichern".

Hinweis:
    - citations_template.json kann (optional) ein Feld "zotero_key" pro Eintrag enthalten.
    - Zotero-Anbindung ist optional; ohne gültige Konfiguration/pyzotero funktioniert das GUI
      trotzdem, nur ohne "Von Zotero holen".
"""

import json
from pathlib import Path
from typing import List, Dict, Any, Optional

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# Zotero ist optional
try:
    from pyzotero import zotero as pyzotero
    HAVE_PYZOTERO = True
except Exception:
    HAVE_PYZOTERO = False


# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path("/Users/programming/PycharmProjects/Find_Bibliography_NEw")

# Standard-Datei für das Template
CITATIONS_TEMPLATE_PATH = PROJECT_ROOT / "mark_quotable" / "citations_template.json"

# Zotero-Konfiguration (optional)
ZOTERO_ENABLED = True  # auf False setzen, wenn du gar keine Zotero-Anbindung willst
ZOTERO_LIBRARY_ID = ""  # z.B. "1234567"
ZOTERO_LIBRARY_TYPE = "user"  # "user" oder "group"
ZOTERO_API_KEY = ""  # dein Zotero-API-Key (Better safe: .env oder OS-Env-Var nutzen)

# ---------------------------------------------------------------------------
# Datenmodell
# ---------------------------------------------------------------------------


class CitationEntry:
    """
    Eine Zeile aus citations_template.json

    Felder im JSON:
      paragraph_index: int
      sentence_index: int
      sentence_text: str
      footnote: str
      zotero_key: Optional[str]  (kann fehlen)
    """

    def __init__(self, data: Dict[str, Any]):
        self.paragraph_index: int = int(data.get("paragraph_index", 0))
        self.sentence_index: int = int(data.get("sentence_index", 0))
        self.sentence_text: str = data.get("sentence_text", "")
        self.footnote: str = data.get("footnote", "")
        self.zotero_key: str = data.get("zotero_key", "")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "paragraph_index": self.paragraph_index,
            "sentence_index": self.sentence_index,
            "sentence_text": self.sentence_text,
            "footnote": self.footnote,
            "zotero_key": self.zotero_key,
        }


def load_citations(path: Path) -> List[CitationEntry]:
    if not path.exists():
        raise FileNotFoundError(f"Template nicht gefunden: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return [CitationEntry(item) for item in data]


def save_citations(path: Path, entries: List[CitationEntry]) -> None:
    data = [e.to_dict() for e in entries]
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Zotero-Client
# ---------------------------------------------------------------------------


class ZoteroClient:
    def __init__(self):
        self.enabled = ZOTERO_ENABLED and HAVE_PYZOTERO and bool(ZOTERO_LIBRARY_ID) and bool(ZOTERO_API_KEY)
        self._client = None
        if self.enabled:
            try:
                self._client = pyzotero.Zotero(ZOTERO_LIBRARY_ID, ZOTERO_LIBRARY_TYPE, ZOTERO_API_KEY)
            except Exception as e:
                print(f"[WARN] Zotero-Initialisierung fehlgeschlagen: {e}")
                self.enabled = False

    def is_enabled(self) -> bool:
        return self.enabled

    def build_footnote_from_key(self, item_key: str) -> Optional[str]:
        """
        Holt ein Item von Zotero und baut eine einfache Fussnote.
        Du kannst das Format später an die Vorgaben deines Seminars anpassen.
        """
        if not self.enabled or not self._client:
            return None
        item_key = item_key.strip()
        if not item_key:
            return None
        try:
            item = self._client.item(item_key)
        except Exception as e:
            print(f"[WARN] Zotero-Item nicht gefunden: {e}")
            return None

        data = item.get("data", {})

        # Autoren
        creators = data.get("creators", [])
        authors = []
        for c in creators:
            if c.get("creatorType") == "author":
                last_name = c.get("lastName", "")
                first_name = c.get("firstName", "")
                if last_name and first_name:
                    authors.append(f"{first_name} {last_name}")
                elif last_name:
                    authors.append(last_name)
        authors_str = ", ".join(authors) if authors else ""

        title = data.get("title", "")
        place = data.get("place", "") or data.get("publisherPlace", "")
        date = data.get("date", "")
        # Jahr aus Datum extrahieren
        year = ""
        for token in date.split():
            if token.isdigit() and len(token) == 4:
                year = token
                break
        if not year:
            year = date

        publisher = data.get("publisher", "")

        # Sehr einfaches Format, zum Schluss kannst du die genaue Form anpassen
        parts = []
        if authors_str:
            parts.append(authors_str)
        if title:
            parts.append(title)
        if place or year:
            paren = " ".join([p for p in [place, year] if p])
            parts.append(f"({paren})")
        if publisher:
            parts.append(publisher)

        note = ", ".join([p for p in parts if p])
        return note if note else None


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------


class CitationGUI(tk.Tk):
    def __init__(self, template_path: Path):
        super().__init__()
        self.title("Citation Editor mit Zotero-Anbindung")
        self.geometry("1100x700")
        self.minsize(900, 600)

        self.template_path = template_path
        self.entries: List[CitationEntry] = []
        self.current_index: Optional[int] = None

        self.zotero_client = ZoteroClient()

        self._build_style()
        self._build_widgets()
        self._load_initial_data()

    # --------------------- UI-Aufbau ---------------------

    def _build_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Treeview", rowheight=26, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        style.configure("TLabel", font=("Segoe UI", 10))
        style.configure("TButton", font=("Segoe UI", 10))

    def _build_widgets(self):
        # Top-Bar
        top_frame = ttk.Frame(self, padding=(10, 10, 10, 5))
        top_frame.pack(side=tk.TOP, fill=tk.X)

        self.path_label_var = tk.StringVar(value=str(self.template_path))
        ttk.Label(top_frame, textvariable=self.path_label_var).pack(side=tk.LEFT, anchor="w")

        ttk.Button(top_frame, text="Template öffnen…", command=self.choose_template).pack(
            side=tk.RIGHT, padx=(5, 0)
        )
        ttk.Button(top_frame, text="Speichern", command=self.save_current).pack(side=tk.RIGHT, padx=(5, 0))

        # Hauptbereich: links Liste, rechts Detail
        main_frame = ttk.Frame(self, padding=(10, 5, 10, 10))
        main_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # Links: Treeview
        left_frame = ttk.Frame(main_frame)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        columns = ("idx", "para", "sent", "preview", "zotero")
        self.tree = ttk.Treeview(
            left_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        self.tree.heading("idx", text="#")
        self.tree.heading("para", text="Abs.")
        self.tree.heading("sent", text="Satz")
        self.tree.heading("preview", text="Satz (Auszug)")
        self.tree.heading("zotero", text="Zotero-Key")

        self.tree.column("idx", width=40, anchor="center", stretch=False)
        self.tree.column("para", width=50, anchor="center", stretch=False)
        self.tree.column("sent", width=50, anchor="center", stretch=False)
        self.tree.column("preview", width=500, anchor="w", stretch=True)
        self.tree.column("zotero", width=150, anchor="w", stretch=False)

        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)

        tree_scroll_y = ttk.Scrollbar(left_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll_y.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll_y.grid(row=0, column=1, sticky="ns")
        left_frame.rowconfigure(0, weight=1)
        left_frame.columnconfigure(0, weight=1)

        # Rechts: Detail-Panel
        right_frame = ttk.Frame(main_frame)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=False)
        right_frame.configure(padding=(15, 5, 0, 0))

        # Meta-Infos
        meta_frame = ttk.Frame(right_frame)
        meta_frame.pack(fill=tk.X)

        self.label_para = ttk.Label(meta_frame, text="Absatz: -")
        self.label_para.grid(row=0, column=0, sticky="w", padx=(0, 20))

        self.label_sent = ttk.Label(meta_frame, text="Satz: -")
        self.label_sent.grid(row=0, column=1, sticky="w")

        # Satz-Text (readonly)
        ttk.Label(right_frame, text="Satz:").pack(anchor="w", pady=(10, 0))
        self.text_sentence = tk.Text(right_frame, height=6, wrap="word")
        self.text_sentence.configure(font=("Segoe UI", 10))
        self.text_sentence.config(state="disabled")
        self.text_sentence.pack(fill=tk.BOTH, expand=False)

        # Zotero-Key
        zotero_frame = ttk.Frame(right_frame)
        zotero_frame.pack(fill=tk.X, pady=(10, 0))

        ttk.Label(zotero_frame, text="Zotero-Key (Item key):").grid(row=0, column=0, sticky="w")
        self.entry_zotero_key = ttk.Entry(zotero_frame, width=25)
        self.entry_zotero_key.grid(row=0, column=1, sticky="ew", padx=(5, 0))

        btn_zotero = ttk.Button(zotero_frame, text="Von Zotero holen", command=self.fill_from_zotero)
        btn_zotero.grid(row=0, column=2, sticky="e", padx=(10, 0))

        zotero_frame.columnconfigure(1, weight=1)

        if not self.zotero_client.is_enabled():
            btn_zotero.state(["disabled"])

        # Fussnote-Text
        ttk.Label(right_frame, text="Fussnote:").pack(anchor="w", pady=(10, 0))
        self.text_footnote = tk.Text(right_frame, height=10, wrap="word")
        self.text_footnote.configure(font=("Segoe UI", 10))
        self.text_footnote.pack(fill=tk.BOTH, expand=True)

        # Navigationsbuttons
        nav_frame = ttk.Frame(right_frame)
        nav_frame.pack(fill=tk.X, pady=(10, 0))

        ttk.Button(nav_frame, text="Vorheriger", command=self.goto_prev).pack(side=tk.LEFT)
        ttk.Button(nav_frame, text="Nächster", command=self.goto_next).pack(side=tk.LEFT, padx=(5, 0))

    # --------------------- Daten laden / speichern ---------------------

    def _load_initial_data(self):
        try:
            self.entries = load_citations(self.template_path)
        except Exception as e:
            messagebox.showerror("Fehler", f"Template konnte nicht geladen werden:\n{e}")
            self.entries = []
        self.refresh_tree()
        if self.entries:
            self.select_index(0)

    def refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        for idx, entry in enumerate(self.entries):
            preview = entry.sentence_text.replace("\n", " ")
            if len(preview) > 80:
                preview = preview[:77] + "..."
            self.tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(
                    idx + 1,
                    entry.paragraph_index,
                    entry.sentence_index,
                    preview,
                    entry.zotero_key,
                ),
            )

    def save_current(self):
        # Aktuellen Eintrag aus dem Detail-Panel zurückschreiben
        self._store_current_entry()
        try:
            save_citations(self.template_path, self.entries)
        except Exception as e:
            messagebox.showerror("Fehler", f"Speichern fehlgeschlagen:\n{e}")
            return
        self.refresh_tree()
        messagebox.showinfo("Gespeichert", f"Template gespeichert:\n{self.template_path}")

    def choose_template(self):
        filename = filedialog.askopenfilename(
            title="citations_template.json wählen",
            filetypes=[("JSON-Dateien", "*.json"), ("Alle Dateien", "*.*")],
            initialdir=str(self.template_path.parent),
        )
        if not filename:
            return
        self.template_path = Path(filename)
        self.path_label_var.set(str(self.template_path))
        try:
            self.entries = load_citations(self.template_path)
        except Exception as e:
            messagebox.showerror("Fehler", f"Template konnte nicht geladen werden:\n{e}")
            self.entries = []
        self.current_index = None
        self.refresh_tree()
        if self.entries:
            self.select_index(0)

    # --------------------- Auswahl / Navigation ---------------------

    def on_tree_select(self, event):
        selection = self.tree.selection()
        if not selection:
            return
        idx = int(selection[0])
        self.select_index(idx)

    def select_index(self, idx: int):
        if idx < 0 or idx >= len(self.entries):
            return
        self._store_current_entry()
        self.current_index = idx
        entry = self.entries[idx]

        # Tree selection setzen
        self.tree.selection_set(str(idx))
        self.tree.see(str(idx))

        # Meta
        self.label_para.config(text=f"Absatz: {entry.paragraph_index}")
        self.label_sent.config(text=f"Satz: {entry.sentence_index}")

        # Satz-Text
        self.text_sentence.config(state="normal")
        self.text_sentence.delete("1.0", tk.END)
        self.text_sentence.insert("1.0", entry.sentence_text)
        self.text_sentence.config(state="disabled")

        # Zotero-Key
        self.entry_zotero_key.delete(0, tk.END)
        if entry.zotero_key:
            self.entry_zotero_key.insert(0, entry.zotero_key)

        # Fussnote
        self.text_footnote.delete("1.0", tk.END)
        if entry.footnote:
            self.text_footnote.insert("1.0", entry.footnote)

    def _store_current_entry(self):
        if self.current_index is None:
            return
        if not (0 <= self.current_index < len(self.entries)):
            return
        entry = self.entries[self.current_index]

        # Zotero-Key
        entry.zotero_key = self.entry_zotero_key.get().strip()

        # Fussnote
        footnote_text = self.text_footnote.get("1.0", tk.END).strip()
        entry.footnote = footnote_text

    def goto_prev(self):
        if self.current_index is None:
            return
        self.select_index(max(0, self.current_index - 1))

    def goto_next(self):
        if self.current_index is None:
            return
        self.select_index(min(len(self.entries) - 1, self.current_index + 1))

    # --------------------- Zotero-Integration ---------------------

    def fill_from_zotero(self):
        if not self.zotero_client.is_enabled():
            messagebox.showwarning(
                "Zotero nicht verfügbar",
                "Zotero-Anbindung ist nicht korrekt konfiguriert oder pyzotero ist nicht installiert.",
            )
            return
        if self.current_index is None:
            return
        key = self.entry_zotero_key.get().strip()
        if not key:
            messagebox.showwarning("Kein Key", "Bitte zuerst einen Zotero-Item-Key eintragen.")
            return

        suggestion = self.zotero_client.build_footnote_from_key(key)
        if not suggestion:
            messagebox.showerror("Fehler", f"Kein Zotero-Eintrag für Key '{key}' gefunden.")
            return

        # Bestehende Fussnote ggf. ergänzen oder ersetzen
        current_text = self.text_footnote.get("1.0", tk.END).strip()
        if current_text:
            # Anhängen, du kannst das Verhalten bei Bedarf anpassen
            new_text = current_text + "\n" + suggestion
        else:
            new_text = suggestion

        self.text_footnote.delete("1.0", tk.END)
        self.text_footnote.insert("1.0", new_text)

        # Direkt im Modell speichern
        self._store_current_entry()
        self.refresh_tree()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main():
    app = CitationGUI(CITATIONS_TEMPLATE_PATH)
    app.mainloop()


if __name__ == "__main__":
    main()