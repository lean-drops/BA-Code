from __future__ import annotations
from typing import Optional, List, Tuple
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QGradient
from .theme import Theme
from .utils import is_dead

def _qcolor(hex_code: str, alpha: Optional[int] = None) -> QtGui.QColor:
    c = QtGui.QColor(hex_code)
    if alpha is not None: c.setAlpha(max(0,min(255,int(alpha))))
    return c

def _linear_gradient(c1: str, c2: str) -> QtGui.QLinearGradient:
    g = QtGui.QLinearGradient(0.0, 0.0, 0.0, 1.0)
    g.setCoordinateMode(QGradient.ObjectBoundingMode)
    g.setColorAt(0.0, QtGui.QColor(c1)); g.setColorAt(1.0, QtGui.QColor(c2))
    return g

def _add_soft_shadow(item: QtWidgets.QGraphicsItem, strength: int = 18, dy: int = 2, alpha: int = 160) -> None:
    try:
        eff = QtWidgets.QGraphicsDropShadowEffect()
        eff.setBlurRadius(max(0, strength)); eff.setOffset(0, dy)
        eff.setColor(QtGui.QColor(0,0,0,max(0,min(255,alpha))))
        if isinstance(item,(QtWidgets.QGraphicsEllipseItem,QtWidgets.QGraphicsPolygonItem,QtWidgets.QGraphicsPathItem)):
            item.setGraphicsEffect(eff)
    except Exception:
        pass

class EdgeItem(QtWidgets.QGraphicsPathItem):
    def __init__(self, a: QtWidgets.QGraphicsItem, b: QtWidgets.QGraphicsItem, weight: float, theme: Theme):
        super().__init__()
        self.a=a; self.b=b; self.weight=float(max(1.0,weight)); self._theme=theme
        self.setZValue(-100)
        alpha=max(40,min(220,int(60+50*Qt.qAbs(Qt.qLn(1.0+self.weight)))))  # ok
        pen=QtGui.QPen(_qcolor(theme.edge,alpha)); pen.setWidthF(max(1.0,0.6+self.weight**0.5)); pen.setCosmetic(True)
        self.setPen(pen); self.setCacheMode(QtWidgets.QGraphicsItem.DeviceCoordinateCache)
        self.update_position()

    def update_position(self) -> None:
        if is_dead(self.a) or is_dead(self.b): return
        p1=self.a.pos(); p2=self.b.pos(); dx=p2.x()-p1.x(); k=0.18*abs(dx)
        c1=QtCore.QPointF(p1.x()+0.25*dx, p1.y()-k); c2=QtCore.QPointF(p2.x()-0.25*dx, p2.y()+k)
        path=QtGui.QPainterPath(p1); path.cubicTo(c1,c2,p2); self.setPath(path)

    def set_highlight(self, on: bool, theme: Theme) -> None:
        if is_dead(self): return
        pen=self.pen()
        if on:
            pen.setColor(_qcolor(theme.edge_sel,230)); pen.setWidthF(self.pen().widthF()+1.0)
        else:
            c=self.pen().color(); base=_qcolor(self._theme.edge,c.alpha())
            pen.setColor(base); pen.setWidthF(max(1.0,0.6+self.weight**0.5))
        self.setPen(pen)

class _BaseLabelMixin:
    name: str
    label: QtWidgets.QGraphicsSimpleTextItem
    _label_bg_rect: QtWidgets.QGraphicsRectItem
    _base_stroke: str
    edges: List[EdgeItem]

    def set_highlight(self, on: bool) -> None:
        if is_dead(self): return
        pen=self.pen(); pen.setWidth(3 if on else 1); pen.setCosmetic(True); self.setPen(pen)
        for e in getattr(self,"edges",[]):
            if not is_dead(e):
                scene=self.scene(); theme: Theme=getattr(scene,"_theme",Theme.dark()) if scene else Theme.dark()
                e.set_highlight(on, theme)

    def apply_emphasis(self, scale: float = 1.0, outline: Optional[QtGui.QColor] = None) -> None:
        if is_dead(self): return
        self.setScale(max(0.1,float(scale)))
        pen=self.pen(); pen.setWidth(2 if outline else 1)
        if outline: pen.setColor(outline)
        pen.setCosmetic(True); self.setPen(pen)

    def toggle_label(self, show: bool) -> None:
        if is_dead(self.label) or is_dead(self._label_bg_rect): return
        self.label.setText(self.name if show else "")
        self._label_bg_rect.setVisible(show)
        if show: self._update_label_bg()

    def _update_label_bg(self) -> None:
        br=self.label.boundingRect(); pad=3.0
        r=QtCore.QRectF(br.x()-pad, br.y()-pad, br.width()+2*pad, br.height()+2*pad)
        self._label_bg_rect.setRect(r)

    def _set_label_offset(self, dx: float, dy: float) -> None:
        self.label.setPos(dx,dy); self._update_label_bg()

class ChronikNode(QtWidgets.QGraphicsEllipseItem, _BaseLabelMixin):
    def __init__(self, name: str, pos: tuple[float,float], size: float, theme: Theme, show_label: bool):
        super().__init__(-size, -size, 2*size, 2*size)
        self.name=name; self._base_stroke=theme.chronik_stroke
        self.setPos(pos[0]*300.0, pos[1]*300.0); self.setAcceptHoverEvents(True)
        # Medaillon: Grundverlauf
        self.setBrush(QtGui.QBrush(_linear_gradient(theme.chronik_fill_hi,theme.chronik_fill_lo)))
        pen=QtGui.QPen(_qcolor(theme.chronik_stroke)); pen.setWidth(1); pen.setCosmetic(True); self.setPen(pen)
        # Bevel-Ring
        self._bevel = QtWidgets.QGraphicsEllipseItem(self)
        self._bevel.setRect(self.rect().adjusted(2,2,-2,-2))
        self._bevel.setPen(QtGui.QPen(Qt.NoPen))
        gloss = QtGui.QRadialGradient(0,-0.3,1.2); gloss.setCoordinateMode(QGradient.ObjectBoundingMode)
        gloss.setColorAt(0.0, QtGui.QColor(255,255,255,90)); gloss.setColorAt(1.0, QtGui.QColor(255,255,255,0))
        self._bevel.setBrush(gloss)
        # Gruppenring
        self._group_ring = QtWidgets.QGraphicsEllipseItem(self)
        self._group_ring.setRect(self.rect().adjusted(-8,-8,8,8))
        gp = QtGui.QPen(Qt.NoPen); gp.setCosmetic(True); self._group_ring.setPen(gp)
        self._group_ring.setBrush(QtGui.QBrush(QtCore.Qt.NoBrush)); self._group_ring.setZValue(40)
        # Label
        self._label_bg_rect = QtWidgets.QGraphicsRectItem(self)
        self._label_bg_rect.setBrush(QtGui.QBrush(QtGui.QColor(theme.label_bg))); self._label_bg_rect.setOpacity(0.85)
        self._label_bg_rect.setPen(QtGui.QPen(Qt.NoPen))
        self._label_bg_rect.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
        self._label_bg_rect.setZValue(90); self._label_bg_rect.setVisible(show_label)
        self.label = QtWidgets.QGraphicsSimpleTextItem(name if show_label else "", self)
        self.label.setBrush(QtGui.QBrush(QtGui.QColor(theme.label)))
        self.label.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
        self.label.setZValue(100); self._set_label_offset(size+6,-8)
        # Interaktives
        self.setCacheMode(QtWidgets.QGraphicsItem.DeviceCoordinateCache)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QtWidgets.QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, True)
        self.edges=[]; self.setToolTip(f"Chronik: {name}")
        _add_soft_shadow(self,16,2,140)

    def itemChange(self, change, value):
        if change==QtWidgets.QGraphicsItem.ItemPositionHasChanged:
            for e in list(self.edges):
                if not is_dead(e): e.update_position()
        return super().itemChange(change,value)

class WorkNode(QtWidgets.QGraphicsPolygonItem, _BaseLabelMixin):
    def __init__(self, name: str, pos: tuple[float,float], size: float, theme: Theme, show_label: bool):
        super().__init__()
        self.name=name; self._base_stroke=theme.work_stroke
        r=size
        pts=[QtCore.QPointF(0,-r), QtCore.QPointF(-0.9*r,0.55*r), QtCore.QPointF(0.0,0.25*r), QtCore.QPointF(0.9*r,0.55*r)]
        self.setPolygon(QtGui.QPolygonF(pts))
        self.setPos(pos[0]*300.0, pos[1]*300.0); self.setAcceptHoverEvents(True)
        self.setBrush(QtGui.QBrush(_linear_gradient(theme.work_fill_hi,theme.work_fill_lo)))
        pen=QtGui.QPen(_qcolor(theme.work_stroke)); pen.setWidth(1); pen.setCosmetic(True); self.setPen(pen)
        # Wimpel-Akzent
        accent=QtWidgets.QGraphicsPolygonItem(self)
        ap=[QtCore.QPointF(0,-r*0.85), QtCore.QPointF(-0.16*r,-r*0.45), QtCore.QPointF(0.16*r,-r*0.45)]
        accent.setPolygon(QtGui.QPolygonF(ap)); accent.setBrush(QtGui.QBrush(_qcolor("#7a4f10"))); accent.setPen(QtGui.QPen(Qt.NoPen))
        accent.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
        # Gruppenring
        scale=1.18; ring=[QtCore.QPointF(p.x()*scale,p.y()*scale) for p in pts]
        self._group_ring=QtWidgets.QGraphicsPolygonItem(QtGui.QPolygonF(ring), self)
        gp=QtGui.QPen(Qt.NoPen); gp.setCosmetic(True); self._group_ring.setPen(gp)
        self._group_ring.setBrush(QtGui.QBrush(QtCore.Qt.NoBrush)); self._group_ring.setZValue(40)
        # Label
        self._label_bg_rect = QtWidgets.QGraphicsRectItem(self)
        self._label_bg_rect.setBrush(QtGui.QBrush(QtGui.QColor(theme.label_bg))); self._label_bg_rect.setOpacity(0.85)
        self._label_bg_rect.setPen(QtGui.QPen(Qt.NoPen))
        self._label_bg_rect.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
        self._label_bg_rect.setZValue(90); self._label_bg_rect.setVisible(show_label)
        self.label = QtWidgets.QGraphicsSimpleTextItem(name if show_label else "", self)
        self.label.setBrush(QtGui.QBrush(QtGui.QColor(theme.label)))
        self.label.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
        self.label.setZValue(100); self._set_label_offset(r+8,-10)
        # Interaktives
        self.setCacheMode(QtWidgets.QGraphicsItem.DeviceCoordinateCache)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QtWidgets.QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, True)
        self.edges=[]; self.setToolTip(f"Werk/PDF: {name}")
        _add_soft_shadow(self,16,2,140)

