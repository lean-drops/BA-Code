from __future__ import annotations
import math
from typing import Dict, Tuple, List, Optional
from PyQt5 import QtCore, QtGui, QtWidgets
from .nodes import ChronikNode, WorkNode, EdgeItem, _qcolor
from .theme import Theme
from .utils import debug, is_dead

def _center_from_pos(pos: Dict[str, Tuple[float,float]]) -> Tuple[float,float]:
    if not pos: return (0.0,0.0)
    cx=sum(x for x,_ in pos.values())/len(pos)*300.0
    cy=sum(y for _,y in pos.values())/len(pos)*300.0
    return (cx,cy)

def _place_labels_radially(node_items: Dict[str, QtWidgets.QGraphicsItem], center: Tuple[float,float]) -> None:
    cx,cy=center
    for it in node_items.values():
        if not isinstance(it,(ChronikNode,WorkNode)): continue
        px,py=it.pos().x(), it.pos().y()
        dx,dy=px-cx, py-cy; r=math.hypot(dx,dy) or 1.0; ux,uy=dx/r, dy/r
        base=(it.boundingRect().width() if isinstance(it,ChronikNode) else it.boundingRect().height())*0.6 + (12 if isinstance(it,ChronikNode) else 14)
        it._set_label_offset(ux*base, uy*base*0.6)

def _rect_scene(lbl: QtWidgets.QGraphicsSimpleTextItem)->QtCore.QRectF:
    return lbl.mapToScene(lbl.boundingRect()).boundingRect()
def _rect_node_scene(node: QtWidgets.QGraphicsItem)->QtCore.QRectF:
    return node.mapToScene(node.boundingRect()).boundingRect()

def _resolve_label_collisions(node_items: Dict[str,QtWidgets.QGraphicsItem], center: Tuple[float,float], iterations=90, step=6.0)->int:
    labels=[]; owners=[]
    for it in node_items.values():
        if isinstance(it,(ChronikNode,WorkNode)) and not is_dead(it.label) and it.label.text():
            labels.append(it.label); owners.append(it)
    moved_total=0; cx,cy=center
    for _ in range(iterations):
        moved=0
        for i in range(len(labels)):
            for j in range(i+1,len(labels)):
                li,lj=labels[i],labels[j]; ri,_rj=_rect_scene(li),_rect_scene(lj)
                if not ri.intersects(_rj): continue
                ci, cj = ri.center(), _rj.center()
                dx=cj.x()-ci.x(); dy=cj.y()-ci.y(); dist=math.hypot(dx,dy) or 1.0
                ux,uy=dx/dist, dy/dist
                rix,riy=(ci.x()-cx,ci.y()-cy); rjx,rjy=(cj.x()-cx,cj.y()-cy)
                nrix=math.hypot(rix,riy) or 1.0; nrjx=math.hypot(rjx,rjy) or 1.0
                rx_i,ry_i=rix/nrix, riy/nrix; rx_j,ry_j=rjx/nrjx, rjy/nrjx
                li.setPos(li.pos().x()+(-ux+rx_i)*step, li.pos().y()+(-uy+ry_i)*step)
                lj.setPos(lj.pos().x()+(+ux+rx_j)*step, lj.pos().y()+(+uy+ry_j)*step)
                if isinstance(owners[i],(ChronikNode,WorkNode)): owners[i]._update_label_bg()
                if isinstance(owners[j],(ChronikNode,WorkNode)): owners[j]._update_label_bg()
                moved+=1
        moved_total+=moved
        if moved==0: break
    return moved_total

def _resolve_label_node_collisions(node_items: Dict[str,QtWidgets.QGraphicsItem], center: Tuple[float,float], iterations=60, step=6.0)->int:
    labels=[]; owners=[]; nodes=[it for it in node_items.values() if isinstance(it,(ChronikNode,WorkNode))]
    for it in nodes:
        if not is_dead(it.label) and it.label.text(): labels.append(it.label); owners.append(it)
    moved_total=0; cx,cy=center
    for _ in range(iterations):
        moved=0
        for li,oi in zip(labels,owners):
            ri=_rect_scene(li); ci=ri.center()
            for node in nodes:
                if node is oi: continue
                rn=_rect_node_scene(node)
                if not ri.intersects(rn): continue
                dx=ci.x()-rn.center().x(); dy=ci.y()-rn.center().y()
                dist=math.hypot(dx,dy) or 1.0; ux,uy=dx/dist, dy/dist
                rx=(ci.x()-cx); ry=(ci.y()-cy); nr=math.hypot(rx,ry) or 1.0; rx/=nr; ry/=nr
                li.setPos(li.pos().x()+(ux+rx)*step, li.pos().y()+(uy+ry)*step)
                if isinstance(oi,(ChronikNode,WorkNode)): oi._update_label_bg()
                moved+=1
        moved_total+=moved
        if moved==0: break
    return moved_total

def _separate_nodes_for_labels(node_items: Dict[str,QtWidgets.QGraphicsItem], iterations=32, step=3.2, margin=2.0)->int:
    nodes=[it for it in node_items.values() if isinstance(it,(ChronikNode,WorkNode))]
    def _label_rect(it)->Optional[QtCore.QRectF]:
        if isinstance(it,(ChronikNode,WorkNode)) and not is_dead(it.label) and it.label.text():
            r=it.label.mapToScene(it.label.boundingRect()).boundingRect()
            return r.adjusted(-margin,-margin,margin,margin)
        return None
    moved_total=0
    for _ in range(iterations):
        moved=0
        for i,a in enumerate(nodes):
            ra=_label_rect(a)
            if ra is None: continue
            ca=ra.center()
            for j in range(i+1,len(nodes)):
                b=nodes[j]; rb=_label_rect(b)
                if rb is None or not ra.intersects(rb): continue
                cb=rb.center(); dx=cb.x()-ca.x(); dy=cb.y()-ca.y(); dist=max(1.0,(dx*dx+dy*dy)**0.5)
                ux,uy=dx/dist, dy/dist
                a.setPos(a.pos().x()-ux*step, a.pos().y()-uy*step)
                b.setPos(b.pos().x()+ux*step, b.pos().y()+uy*step); moved+=1
        moved_total+=moved
        if moved==0: break
    return moved_total

def enforce_label_clearance(node_items: Dict[str,QtWidgets.QGraphicsItem], center: Tuple[float,float]) -> None:
    _place_labels_radially(node_items, center)
    for t in range(7):
        m1=_resolve_label_collisions(node_items, center, iterations=90, step=6.0*(0.72**t))
        m2=_resolve_label_node_collisions(node_items, center, iterations=60, step=6.0*(0.72**t))
        m3=_separate_nodes_for_labels(node_items, iterations=32, step=3.2*(0.72**t))
        debug(f"[labels] pass={t} moved: ll={m1} ln={m2} nn={m3}")
        if (m1+m2+m3)==0: break

def make_scene(G, positions, min_edge_weight: int, show_labels: bool, theme: Theme|None=None)->QtWidgets.QGraphicsScene:
    theme=theme or Theme.dark()
    scene=QtWidgets.QGraphicsScene(); scene._theme=theme; scene._show_labels=bool(show_labels)
    scene.setBackgroundBrush(QtGui.QBrush(QtGui.QColor(theme.bg)))
    node_items={}
    # Nodes
    for n,d in G.nodes(data=True):
        role=d.get("role"); label=d.get("label",str(n)); pos=positions.get(n,(0.0,0.0))
        if role=="chronik":
            size=7.0+2.8*math.sqrt(max(1,int(d.get("mentions",1))))
            item=ChronikNode(label,pos,size,theme,show_label=show_labels)
        else:
            size=11.0+3.2*math.sqrt(max(1,int(d.get("items",1))))
            item=WorkNode(label,pos,size,theme,show_label=show_labels)
        node_items[n]=item; scene.addItem(item)
    # Edges
    kept=0
    for u,v,ed in G.edges(data=True):
        w=float(ed.get("weight",1.0))
        if w<min_edge_weight: continue
        a=node_items[u]; b=node_items[v]; e=EdgeItem(a,b,w,theme)
        if isinstance(a,(ChronikNode,WorkNode)): a.edges.append(e)
        if isinstance(b,(ChronikNode,WorkNode)): b.edges.append(e)
        scene.addItem(e); kept+=1
    center=_center_from_pos(positions); enforce_label_clearance(node_items, center)
    bbox=scene.itemsBoundingRect().adjusted(-600,-600,600,600)  # groß genug → keine abgeschnittenen Titel
    scene.setSceneRect(bbox)
    debug(f"[scene] erstellt: nodes={len(node_items)} edges_visible={kept} rect={bbox}")
    return scene

