from __future__ import annotations
import os, sys, traceback
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt
import networkx as nx
from .utils import debug
from .io import load_mentions_csv, resolve_columns, build_bipartite_graph
from .layouts import LayoutParams, compute_layout
from .scene import make_scene
from .canvas import GraphCanvas
from .theme import Theme

class MainWindow(QtWidgets.QMainWindow):
    SETTINGS_KEY_LAST_CSV="last_csv_path"
    def __init__(self):
        super().__init__()
        self.setWindowTitle("chroniken_library↔Werke — Interaktives Netz")
        self.resize(1400, 860)
        self.csv_path=None; self.df=None; self.G: nx.Graph|None=None
        self.layout_name="bipartite"; self.min_w=1; self.show_labels=True
        self.lparams=LayoutParams()
        self._build_ui(); self._load_last_csv_if_available()

    def _build_ui(self):
        central=QtWidgets.QWidget(); self.setCentralWidget(central)
        self.canvas=GraphCanvas()
        self.btn_load_csv=QtWidgets.QPushButton("CSV laden…"); self.btn_load_csv.clicked.connect(self.on_load_csv)
        self.cmb_layout=QtWidgets.QComboBox(); self.cmb_layout.addItems(["bipartite","spring","kamada_kawai","forceatlas2"])
        self.cmb_layout.currentTextChanged.connect(self.on_layout_change)
        self.slider=QtWidgets.QSlider(Qt.Horizontal); self.slider.setRange(1,10); self.slider.setValue(self.min_w); self.slider.valueChanged.connect(self.on_slider)
        self.lbl_thresh=QtWidgets.QLabel(f"Kantenschwelle: ≥ {self.min_w}")
        self.chk_labels=QtWidgets.QCheckBox("Labels immer sichtbar"); self.chk_labels.setChecked(self.show_labels); self.chk_labels.stateChanged.connect(self.on_labels_toggle)
        self.chk_invert_focus=QtWidgets.QCheckBox("Nicht-verbundene hervorheben (Gegenrolle)"); self.chk_invert_focus.stateChanged.connect(self.on_invert_focus_toggle)
        self.sld_gap=QtWidgets.QSlider(Qt.Horizontal); self.sld_gap.setRange(150,600); self.sld_gap.setValue(int(self.lparams.bipartite_gap_x*100)); self.sld_gap.valueChanged.connect(self.on_gap_change)
        self.lbl_gap=QtWidgets.QLabel(f"Abstand L↔R: {self.lparams.bipartite_gap_x:.2f}")
        self.spin_sim_thr=QtWidgets.QDoubleSpinBox(); self.spin_sim_thr.setRange(0.0,1.0); self.spin_sim_thr.setSingleStep(0.05); self.spin_sim_thr.setValue(self.lparams.similarity_threshold); self.spin_sim_thr.valueChanged.connect(self.on_similarity_change)
        self.spin_sim_strength=QtWidgets.QDoubleSpinBox(); self.spin_sim_strength.setRange(0.0,5.0); self.spin_sim_strength.setSingleStep(0.1); self.spin_sim_strength.setValue(self.lparams.similarity_strength); self.spin_sim_strength.valueChanged.connect(self.on_similarity_change)
        self.chk_use_sim=QtWidgets.QCheckBox("Ähnlichkeit für Layout nutzen"); self.chk_use_sim.setChecked(self.lparams.use_similarity); self.chk_use_sim.stateChanged.connect(self.on_similarity_change)
        self.search_edit=QtWidgets.QLineEdit(); self.search_edit.setPlaceholderText("Knoten suchen…")
        self.btn_search=QtWidgets.QPushButton("Hervorheben"); self.btn_search.clicked.connect(self.on_search)

        left=QtWidgets.QVBoxLayout(); left.setSpacing(10)
        left.addWidget(self.btn_load_csv)
        row1=QtWidgets.QHBoxLayout(); row1.addWidget(QtWidgets.QLabel("Layout:")); row1.addWidget(self.cmb_layout,1); left.addLayout(row1)
        left.addWidget(self.lbl_gap); left.addWidget(self.sld_gap)
        gb_sim=QtWidgets.QGroupBox("Ähnlichkeitsanziehung (Layout)"); form=QtWidgets.QFormLayout(gb_sim)
        form.addRow("Schwelle (Jaccard):", self.spin_sim_thr); form.addRow("Stärke:", self.spin_sim_strength); form.addRow(self.chk_use_sim); left.addWidget(gb_sim)
        left.addWidget(self.lbl_thresh); left.addWidget(self.slider); left.addWidget(self.chk_labels); left.addWidget(self.chk_invert_focus)
        left.addWidget(QtWidgets.QLabel("Suche:")); left.addWidget(self.search_edit); left.addWidget(self.btn_search); left.addStretch(1)
        left_box=QtWidgets.QFrame(); left_box.setLayout(left); left_box.setFixedWidth(340)
        left_box.setStyleSheet("""
            QFrame { background:#0f172a; }
            QLabel, QCheckBox, QGroupBox { color:#e2e8f0; }
            QGroupBox { border:1px solid #334155; margin-top:6px; }
            QGroupBox::title { subcontrol-origin: margin; left:8px; padding:0 4px; }
            QPushButton { background:#1e293b; color:#e2e8f0; border:1px solid #334155; padding:6px; border-radius:6px; }
            QPushButton:hover { background:#273449; }
            QComboBox, QLineEdit, QDoubleSpinBox { background:#0b1320; color:#e2e8f0; border:1px solid #334155; padding:5px; border-radius:6px; }
            QSlider::groove:horizontal { height:6px; background:#1f2937; border-radius:3px; }
            QSlider::handle:horizontal { background:#3b82f6; width:14px; height:14px; margin:-4px 0; border-radius:7px; }
        """)
        main=QtWidgets.QHBoxLayout(central); main.setContentsMargins(0,0,0,0); main.addWidget(left_box); main.addWidget(self.canvas,1)
        menu=self.menuBar(); m_file=menu.addMenu("Datei")
        act_csv=m_file.addAction("CSV laden…"); act_csv.triggered.connect(self.on_load_csv)
        m_file.addSeparator(); act_quit=m_file.addAction("Beenden"); act_quit.triggered.connect(self.close)
        self.setStyleSheet("QMainWindow { background:#0b1320; } QMenuBar, QMenu { color:#e2e8f0; background:#0f172a; }")

    def _settings(self)->QtCore.QSettings: return QtCore.QSettings()

    def _save_last_csv(self,path:str)->None:
        s=self._settings(); s.setValue(self.SETTINGS_KEY_LAST_CSV,path); s.sync(); debug(f"[settings] last_csv={path}")

    def _load_last_csv_if_available(self)->None:
        s=self._settings(); path=s.value(self.SETTINGS_KEY_LAST_CSV, type=str)
        if path and os.path.isfile(path):
            try:
                debug(f"[startup] lade zuletzt: {path}")
                self.csv_path=path; self.df=load_mentions_csv(path); self._rebuild()
            except Exception as ex:
                traceback.print_exc(); QtWidgets.QMessageBox.warning(self,"Warnung",f"Letzte CSV konnte nicht geladen werden:\n{ex}")
        elif path:
            debug(f"[startup] gespeicherter Pfad existiert nicht mehr: {path}")

    def _rebuild(self)->None:
        if self.df is None: return
        try:
            doc_col, work_col = resolve_columns(self.df)
            self.G = build_bipartite_graph(self.df, doc_col, work_col)
            pos = compute_layout(self.G, self.layout_name, self.lparams)
            scene = make_scene(self.G, pos, self.min_w, self.show_labels, Theme.dark())
            self.canvas.set_graph_scene(scene)
            title_suffix=f" — {os.path.basename(self.csv_path)}" if self.csv_path else ""
            inv=" [Invertierter Klick-Fokus]" if self.chk_invert_focus.isChecked() else ""
            self.setWindowTitle(f"chroniken_library↔Werke — Interaktives Netz{title_suffix}{inv}")
            debug(f"[rebuild] nodes={self.G.number_of_nodes()} edges={self.G.number_of_edges()} thr={self.min_w} layout={self.layout_name}")
        except Exception as ex:
            traceback.print_exc(); QtWidgets.QMessageBox.critical(self,"Fehler",f"Netzaufbau fehlgeschlagen:\n{ex}")

    # Actions
    def on_load_csv(self)->None:
        path,_=QtWidgets.QFileDialog.getOpenFileName(self,"Mentions-CSV öffnen", os.getcwd(), "CSV Dateien (*.csv)")
        if not path: return
        try:
            self.csv_path=path; self.df=load_mentions_csv(path); self._rebuild(); self._save_last_csv(path)
        except Exception as ex:
            traceback.print_exc(); QtWidgets.QMessageBox.critical(self,"Fehler",f"CSV konnte nicht geladen werden:\n{ex}")

    def on_layout_change(self,text:str)->None:
        self.layout_name=text; debug(f"[ui] layout={text}"); self._rebuild()
    def on_slider(self,val:int)->None:
        self.min_w=int(val); self.lbl_thresh.setText(f"Kantenschwelle: ≥ {self.min_w}"); debug(f"[ui] threshold={self.min_w}"); self._rebuild()
    def on_labels_toggle(self,state:int)->None:
        self.show_labels=(state==Qt.Checked); debug(f"[ui] labels={self.show_labels}"); self._rebuild()
    def on_invert_focus_toggle(self,state:int)->None:
        self.canvas.invert_focus=(state==Qt.Checked)
        if self.canvas._current and self.canvas._belongs_here(self.canvas._current): self.canvas._apply_focus(self.canvas._current, sticky=True)
        title_suffix=f" — {os.path.basename(self.csv_path)}" if self.csv_path else ""; inv=" [Invertierter Klick-Fokus]" if state==Qt.Checked else ""
        self.setWindowTitle(f"chroniken_library↔Werke — Interaktives Netz{title_suffix}{inv}")
        debug(f"[ui] invert_focus={self.canvas.invert_focus}")
    def on_search(self)->None:
        term=self.search_edit.text().strip(); hits=self.canvas.highlight(term, self.show_labels)
        if term and hits==0: QtWidgets.QToolTip.showText(self.mapToGlobal(self.search_edit.pos()), "Kein Treffer", self.search_edit)
    def on_gap_change(self,val:int)->None:
        self.lparams.bipartite_gap_x=max(1.0,val/100.0); self.lbl_gap.setText(f"Abstand L↔R: {self.lparams.bipartite_gap_x:.2f}")
        if self.layout_name=="bipartite": debug(f"[ui] gap={self.lparams.bipartite_gap_x:.2f}"); self._rebuild()
    def on_similarity_change(self)->None:
        self.lparams.similarity_threshold=float(self.spin_sim_thr.value())
        self.lparams.similarity_strength=float(self.spin_sim_strength.value())
        self.lparams.use_similarity=self.chk_use_sim.isChecked()
        debug(f"[ui] sim thr={self.lparams.similarity_threshold:.2f} str={self.lparams.similarity_strength:.2f} use={self.lparams.use_similarity}")
        if self.layout_name!="bipartite": self._rebuild()

def _try_candidates()->str|None:
    env=os.environ.get("CHRONIKEN_MENTIONS_CSV","").strip()
    cands=[env if env else "", os.path.join(os.getcwd(),"chroniken_mentions.csv"),
           os.path.expanduser("~/PycharmProjects/Find_Bibliography_NEw/data/azk_library/chroniken_mentions.csv")]
    for p in cands:
        if p and os.path.isfile(p): return p
    return None

def main()->None:
    debug("[startup] Starte GUI …")
    try:
        QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
        QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)
    except Exception: pass
    app=QtWidgets.QApplication(sys.argv)
    QtCore.QCoreApplication.setOrganizationName("chroniken_library")
    QtCore.QCoreApplication.setApplicationName("ChronikenWerkeGUI")
    win=MainWindow()
    if win.df is None:
        csv=_try_candidates()
        if csv:
            try:
                win.csv_path=csv; win.df=load_mentions_csv(csv); win._rebuild(); win._save_last_csv(csv)
            except Exception as ex:
                traceback.print_exc(); QtWidgets.QMessageBox.warning(win,"Warnung",f"CSV Auto-Laden fehlgeschlagen:\n{ex}")
    win.show(); sys.exit(app.exec_())