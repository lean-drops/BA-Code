from __future__ import annotations
from typing import Dict, List, Tuple, Optional, Set
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt
from .nodes import ChronikNode, WorkNode, EdgeItem, _qcolor
from .theme import Theme, GROUP_COLORS
from .utils import debug, is_dead

class GraphCanvas(QtWidgets.QGraphicsView):
    def __init__(self):
        super().__init__()
        self.setRenderHint(QtGui.QPainter.Antialiasing, True)
        self.setDragMode(QtWidgets.QGraphicsView.ScrollHandDrag)
        self.setViewportUpdateMode(QtWidgets.QGraphicsView.SmartViewportUpdate)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.setStyleSheet("border:0")
        self._nodes: Dict[str, QtWidgets.QGraphicsItem] = {}
        self._theme = Theme.dark()
        self._temp_nodes: List[QtWidgets.QGraphicsItem] = []
        self._temp_edges: List[EdgeItem] = []
        self._sticky_nodes: List[QtWidgets.QGraphicsItem] = []
        self._sticky_edges: List[EdgeItem] = []
        self._current: Optional[QtWidgets.QGraphicsItem] = None
        self._hit_names: List[str] = []; self._hit_index: int = -1
        self.invert_focus: bool = False; self.dim_opacity: float = 0.60
        # Gruppen
        self.active_group:int = 1
        self.groups: Dict[int, Set[QtWidgets.QGraphicsItem]] = {i:set() for i in range(1,7)}

    # Gruppen
    def _group_color(self, idx:int)->QtGui.QColor:
        idx0=max(1,min(6,idx))-1; return QtGui.QColor(GROUP_COLORS[idx0])
    def _apply_group_visual(self, it: QtWidgets.QGraphicsItem, idx:int)->None:
        col=self._group_color(idx)
        if isinstance(it,ChronikNode) and hasattr(it,"_group_ring"):
            pen=QtGui.QPen(col); pen.setCosmetic(True); pen.setWidth(4); it._group_ring.setPen(pen); it._group_ring.setVisible(True)
        if isinstance(it,WorkNode) and hasattr(it,"_group_ring"):
            pen=QtGui.QPen(col); pen.setCosmetic(True); pen.setWidth(4); it._group_ring.setPen(pen); it._group_ring.setVisible(True)
    def assign_to_group(self, it: QtWidgets.QGraphicsItem, idx:int)->None:
        for g in self.groups.values(): g.discard(it)
        self.groups[idx].add(it); self._apply_group_visual(it,idx); debug(f"[group] add idx={idx} name={getattr(it,'name','?')}")
    def clear_group(self, idx:int)->None:
        for it in list(self.groups[idx]):
            if isinstance(it,(ChronikNode,WorkNode)) and hasattr(it,"_group_ring"):
                pen=QtGui.QPen(QtCore.Qt.NoPen); pen.setCosmetic(True); it._group_ring.setPen(pen); it._group_ring.setVisible(False)
        self.groups[idx].clear(); debug(f"[group] clear idx={idx}")
    def clear_all_groups(self)->None:
        for i in list(self.groups.keys()): self.clear_group(i); debug("[group] clear all")
    def set_active_group(self, idx:int)->None:
        self.active_group=max(1,min(6,int(idx))); debug(f"[group] active={self.active_group}")

    def _belongs_here(self, it: QtWidgets.QGraphicsItem)->bool:
        try: return not is_dead(it) and it.scene() is self.scene()
        except Exception: return False

    def _restore_opacity(self)->None:
        for it in list(self._nodes.values()):
            if self._belongs_here(it): it.setOpacity(1.0)

    def _focus_selection(self, node: QtWidgets.QGraphicsItem):
        neighbor_nodes=[node]; neighbor_edges=[]
        if hasattr(node,"edges"):
            for e in list(node.edges):  # type: ignore
                if is_dead(e) or not self._belongs_here(e): continue
                neighbor_edges.append(e)
                other = e.a if e.b is node else e.b
                if isinstance(other,QtWidgets.QGraphicsItem) and self._belongs_here(other): neighbor_nodes.append(other)
        if self.invert_focus:
            if isinstance(node,WorkNode):
                connected={id(it) for it in neighbor_nodes if isinstance(it,ChronikNode)}
                cands=[it for it in self._nodes.values() if isinstance(it,ChronikNode) and self._belongs_here(it)]
                non=[it for it in cands if id(it) not in connected]
            else:
                connected={id(it) for it in neighbor_nodes if isinstance(it,WorkNode)}
                cands=[it for it in self._nodes.values() if isinstance(it,WorkNode) and self._belongs_here(it)]
                non=[it for it in cands if id(it) not in connected]
            debug(f"[focus] invert non-neighbors={len(non)}")
            return [node]+non, []
        return neighbor_nodes, neighbor_edges

    def _apply_focus(self, node: QtWidgets.QGraphicsItem, sticky: bool)->None:
        scn=self.scene()
        if not scn or not self._belongs_here(node): return
        show_labels=bool(getattr(scn,"_show_labels",True))
        theme: Theme=getattr(scn,"_theme",self._theme)
        if not sticky: self._clear_temp_focus()
        nodes,edges=self._focus_selection(node)
        sel_ids=set(map(id,nodes))
        if self.invert_focus:
            for it in list(self._nodes.values()):
                if not self._belongs_here(it): continue
                it.setOpacity(1.0 if id(it) in sel_ids else self.dim_opacity)
        else:
            self._restore_opacity()
        for idx,it in enumerate(nodes):
            if not self._belongs_here(it): continue
            if isinstance(it,(ChronikNode,WorkNode)):
                it.apply_emphasis(scale=1.20 if idx==0 else 1.10, outline=_qcolor(theme.edge_sel))
                if not show_labels: it.toggle_label(True)
                (self._sticky_nodes if sticky else self._temp_nodes).append(it)
        if not self.invert_focus:
            for e in edges:
                if self._belongs_here(e): e.set_highlight(True, theme); (self._sticky_edges if sticky else self._temp_edges).append(e)
        self._current=node; self.viewport().update(); debug(f"[focus] sticky={sticky} nodes={len(nodes)} edges={len(edges)}")

    def _clear_nodes(self, items: List[QtWidgets.QGraphicsItem], restore_labels: bool)->None:
        scn=self.scene(); show_labels=bool(getattr(scn,"_show_labels",True)) if scn else True
        for it in list(items):
            if not self._belongs_here(it): continue
            if isinstance(it,(ChronikNode,WorkNode)):
                it.apply_emphasis(scale=1.0, outline=None)
                if not show_labels and restore_labels: it.toggle_label(False)

    def _clear_edges(self, edges: List[EdgeItem])->None:
        theme: Theme=getattr(self.scene(),"_theme",self._theme) if self.scene() else self._theme
        for e in list(edges):
            if self._belongs_here(e): e.set_highlight(False, theme)

    def _clear_temp_focus(self)->None:
        self._clear_edges(self._temp_edges); self._clear_nodes(self._temp_nodes, True)
        self._temp_edges.clear(); self._temp_nodes.clear()
        if not self.invert_focus: self._restore_opacity()
        self.viewport().update()

    def _clear_sticky_focus(self)->None:
        self._clear_edges(self._sticky_edges); self._clear_nodes(self._sticky_nodes, not bool(getattr(self.scene(),"_show_labels",True)) if self.scene() else True)
        self._sticky_edges.clear(); self._sticky_nodes.clear()
        if not self.invert_focus: self._restore_opacity()
        self.viewport().update()

    def set_graph_scene(self, scene: QtWidgets.QGraphicsScene)->None:
        try:
            self._clear_temp_focus(); self._clear_sticky_focus(); self.clear_all_groups()
        except Exception:
            self._temp_edges.clear(); self._temp_nodes.clear(); self._sticky_edges.clear(); self._sticky_nodes.clear(); self.clear_all_groups()
        super().setScene(scene); self._nodes.clear()
        n=e=0
        for it in scene.items():
            if isinstance(it,(ChronikNode,WorkNode)): self._nodes[it.name]=it; n+=1  # type: ignore
            elif isinstance(it,(QtWidgets.QGraphicsPathItem,QtWidgets.QGraphicsLineItem)): e+=1
        self._theme=getattr(scene,"_theme",Theme.dark())
        self.setStyleSheet(f"QGraphicsView {{ background:{self._theme.bg}; }}")
        self.fitInView(scene.itemsBoundingRect(), Qt.KeepAspectRatio)
        debug(f"[canvas] scene set: nodes={n} edges={e}")

    # Interaction
    def wheelEvent(self, ev: QtGui.QWheelEvent)->None:
        self.scale(1.15 if ev.angleDelta().y()>0 else 1/1.15, 1.15 if ev.angleDelta().y()>0 else 1/1.15)
    def mousePressEvent(self, ev: QtGui.QMouseEvent)->None:
        scn=self.scene()
        if scn and ev.button()==Qt.LeftButton:
            pos=self.mapToScene(ev.pos()); clicked=scn.items(pos)
            target=next((it for it in clicked if isinstance(it,(ChronikNode,WorkNode))), None)
            mods=int(ev.modifiers())
            if target is not None and self._belongs_here(target):
                if mods & Qt.ControlModifier:
                    self.assign_to_group(target, self.active_group)
                else:
                    self._clear_sticky_focus(); self._apply_focus(target, sticky=True)
            else:
                self._clear_sticky_focus(); self._clear_temp_focus(); self._current=None
        super().mousePressEvent(ev)
    def keyPressEvent(self, ev: QtGui.QKeyEvent)->None:
        k=ev.key(); mod=int(ev.modifiers())
        if k in (Qt.Key_Plus, Qt.Key_Equal): self.scale(1.15,1.15); return
        if k==Qt.Key_Minus: self.scale(1/1.15,1/1.15); return
        if k==Qt.Key_F and self.scene(): self.fitInView(self.scene().itemsBoundingRect(), Qt.KeepAspectRatio); return
        if k==Qt.Key_Escape: self._clear_temp_focus(); self._clear_sticky_focus(); self._current=None; return
        if k==Qt.Key_Left: self._directional_jump(-1,0); return
        if k==Qt.Key_Right: self._directional_jump(1,0); return
        if k==Qt.Key_Up: self._directional_jump(0,-1); return
        if k==Qt.Key_Down: self._directional_jump(0,1); return
        if Qt.Key_1<=k<=Qt.Key_6: self.set_active_group(k-Qt.Key_0); return
        if k==Qt.Key_G and self._current and self._belongs_here(self._current): self.assign_to_group(self._current,self.active_group); return
        if k==Qt.Key_C: self.clear_group(self.active_group); return
        if k==Qt.Key_A and (mod & Qt.ControlModifier): self.clear_all_groups(); return
        if k==Qt.Key_Tab and self._hit_names:
            self._hit_index=(self._hit_index+ (1 if (mod & Qt.ShiftModifier)==0 else -1))%len(self._hit_names)
            name=self._hit_names[self._hit_index]; it=self._nodes.get(name)
            if it and self._belongs_here(it): self._apply_focus(it, sticky=True); return
        super().keyPressEvent(ev)

    def _candidate_items(self)->List[QtWidgets.QGraphicsItem]:
        if self._hit_names:
            items=[self._nodes[n] for n in self._hit_names if n in self._nodes and self._belongs_here(self._nodes[n])]
            if items: return items
        return [it for it in self._nodes.values() if self._belongs_here(it)]

    def _view_center_scene(self)->QtCore.QPointF:
        return self.mapToScene(self.viewport().rect().center())

    def _directional_jump(self, vx: float, vy: float)->None:
        cands=self._candidate_items()
        if not cands: return
        import math
        vlen=math.hypot(vx,vy) or 1.0; ux,uy=vx/vlen, vy/vlen
        def score(from_pt: QtCore.QPointF, to_item: QtWidgets.QGraphicsItem)->tuple[float,float]:
            tp=to_item.pos(); dx=tp.x()-from_pt.x(); dy=tp.y()-from_pt.y()
            dist=math.hypot(dx,dy) or 1e-6; proj=(dx*ux+dy*uy)/dist; return proj, dist
        if self._current is None or not self._belongs_here(self._current):
            origin=self._view_center_scene(); best=None; best_key=(-2.0,float("inf"))
            for it in cands:
                s=score(origin,it); key=(s[0], -1.0/s[1])
                if key>best_key: best=it; best_key=key
            if best is not None: self._apply_focus(best, sticky=True)
            return
        origin=self._current.pos(); best=None; best_val=(-2.0,float("inf"))
        for it in cands:
            if it is self._current: continue
            proj,dist=score(origin,it)
            if proj<0.01: continue
            key=(proj, -1.0/dist)
            if key>best_val: best=it; best_val=key
        if best is None:
            for it in cands:
                if it is self._current: continue
                proj,dist=score(origin,it); key=(proj,-1.0/dist)
                if key>best_val: best=it; best_val=key
        if best is not None: self._apply_focus(best, sticky=True)

    def highlight(self, term: str, force_labels: bool)->int:
        term=term.strip().lower(); hits=0; names=[]
        for n,it in list(self._nodes.items()):
            hit=bool(term and term in n.lower())
            if isinstance(it,(ChronikNode,WorkNode)) and self._belongs_here(it):
                it.set_highlight(hit); it.toggle_label(True)  # Labels bleiben sichtbar
            if hit: names.append(n); hits+=1
        # Sortierhilfe
        def _yx(nm:str)->tuple[int,int]:
            it=self._nodes.get(nm)
            if not it: return (0,0)
            p=it.pos(); return (int(round(p.y())), int(round(p.x())))
        self._hit_names=sorted(names,key=_yx); self._hit_index=(-1 if not self._hit_names else 0)
        debug(f"[highlight] term='{term}' hits={hits}")
        return hits

    def export_png(self, path: str)->None:
        if not self.scene(): raise RuntimeError("Keine Szene geladen.")
        rect=self.scene().itemsBoundingRect().adjusted(-40,-40,40,40)
        img=QtGui.QImage(int(rect.width()), int(rect.height()), QtGui.QImage.Format_ARGB32)
        img.fill(QtGui.QColor(self._theme.bg))
        painter=QtGui.QPainter(img); painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        self.scene().render(painter, QtCore.QRectF(img.rect()), rect); painter.end()
        if not img.save(path): raise IOError(f"PNG konnte nicht gespeichert werden: {path}")
        debug(f"[export] png={path} size={img.width()}x{img.height()}")

