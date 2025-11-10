from __future__ import annotations
import math, random
import networkx as nx
from dataclasses import dataclass
from itertools import combinations
from collections import defaultdict
from .io import node_mass, weighted_degree
from .utils import debug

@dataclass
class LayoutParams:
    bipartite_gap_x: float = 2.8
    bipartite_left_vspace: float = 1.25
    bipartite_right_vspace: float = 1.00
    similarity_threshold: float = 0.15
    similarity_strength: float = 1.0
    use_similarity: bool = True

def _bipartite(G: nx.Graph, p: LayoutParams, scale: float = 2.0) -> dict[str, tuple[float,float]]:
    left  = [n for n,d in G.nodes(data=True) if d.get("role")=="work"]
    right = [n for n,d in G.nodes(data=True) if d.get("role")=="chronik"]
    def _order(side, other):
        idx = {n:i for i,n in enumerate(other)}
        def score(n):
            ns = list(G.neighbors(n))
            if not ns: return 0.0
            s=w=0.0
            for m in ns:
                nm = node_mass(G,m)
                s += idx.get(m,0)*nm; w += nm
            return s/(w or 1.0)
        return sorted(side, key=score)
    for _ in range(2):
        left=_order(left,right); right=_order(right,left)
    pos={}
    def place(lst,x,vspace):
        n=max(1,len(lst))
        for i,node in enumerate(lst):
            y=(i-(n-1)/2.0)*vspace
            pos[node]=(x,y)
    place(left,-p.bipartite_gap_x,p.bipartite_left_vspace)
    place(right, p.bipartite_gap_x,p.bipartite_right_vspace)
    return {n:(x*scale,y*scale) for n,(x,y) in pos.items()}

def _similarity_graph(G: nx.Graph, p: LayoutParams) -> nx.Graph:
    if not p.use_similarity: return G
    H = nx.Graph(); H.add_nodes_from(G.nodes(data=True)); H.add_edges_from((u,v,d.copy()) for u,v,d in G.edges(data=True))
    def add(role_a:str):
        inv=defaultdict(list)
        deg={}
        for n,d in G.nodes(data=True):
            if d.get("role")!=role_a: continue
            ns=list(G.neighbors(n)); deg[n]=len(ns)
            for m in ns: inv[m].append(n)
        co=defaultdict(int)
        for lst in inv.values():
            for a,b in combinations(sorted(lst),2): co[(a,b)]+=1
        added=0
        for (a,b),inter in co.items():
            u=deg.get(a,0); v=deg.get(b,0); uni=u+v-inter
            if uni<=0: continue
            j=inter/uni
            if j>=p.similarity_threshold:
                w=float(inter)*p.similarity_strength*(1.0+2.5*j)
                if H.has_edge(a,b): H[a][b]["weight"]=float(H[a][b].get("weight",1.0))+w
                else: H.add_edge(a,b,weight=w)
                added+=1
        debug(f"[sim] role={role_a} added={added} thr={p.similarity_threshold:.2f} str={p.similarity_strength:.2f}")
    add("work"); add("chronik"); return H

def _forceatlas2(H: nx.Graph, init: dict[str,tuple[float,float]]|None, iters=380, gravity=0.06, scaling=1.2, dt=0.08):
    nodes=list(H.nodes()); 
    if not nodes: return {}
    pos={n:list(init[n]) if init and n in init else [random.uniform(-1,1),random.uniform(-1,1)] for n in nodes}
    mass={n:node_mass(H,n) for n in nodes}
    for it in range(iters):
        fx={n:0.0 for n in nodes}; fy={n:0.0 for n in nodes}
        for i in range(len(nodes)):
            n1=nodes[i]; x1,y1=pos[n1]
            for j in range(i+1,len(nodes)):
                n2=nodes[j]; x2,y2=pos[n2]
                dx=x1-x2; dy=y1-y2; dist=(dx*dx+dy*dy)**0.5+1e-9
                f=(scaling*mass[n1]*mass[n2])/dist
                rx=f*(dx/dist); ry=f*(dy/dist)
                fx[n1]+=rx; fy[n1]+=ry; fx[n2]-=rx; fy[n2]-=ry
        for u,v,d in H.edges(data=True):
            w=float(d.get("weight",1.0))
            x1,y1=pos[u]; x2,y2=pos[v]
            dx=x2-x1; dy=y2-y1; dist=(dx*dx+dy*dy)**0.5+1e-9
            f=w*dist; ax=f*(dx/dist); ay=f*(dy/dist)
            fx[u]+=ax; fy[u]+=ay; fx[v]-=ax; fy[v]-=ay
        maxd=0.0
        for n in nodes:
            x,y=pos[n]; inv=1.0/mass[n]
            dx=dt*fx[n]*inv; dy=dt*fy[n]*inv
            x+=dx; y+=dy; pos[n]=[x,y]; maxd=max(maxd,abs(dx)+abs(dy))
            fx[n]+=-gravity*x; fy[n]+=-gravity*y
        if it%60==0: debug(f"[fa2] it={it} maxΔ={maxd:.4f}")
        if maxd<1e-4: break
    return {n:(float(x),float(y)) for n,(x,y) in pos.items()}

def _relax(G: nx.Graph, pos: dict[str,tuple[float,float]], iters=140, base=0.36, step=0.055):
    nodes=list(pos.keys()); 
    if len(nodes)<=1: return pos
    radius={n:0.05+0.02*node_mass(G,n)**0.5 for n in nodes}
    P={n:[float(x),float(y)] for n,(x,y) in pos.items()}
    for _ in range(iters):
        moved=0
        for i,a in enumerate(nodes):
            x1,y1=P[a]; fx=fy=0.0
            for j,b in enumerate(nodes):
                if i==j: continue
                x2,y2=P[b]; dx=x1-x2; dy=y1-y2; dist=(dx*dx+dy*dy)**0.5+1e-6
                want=base+radius[a]+radius[b]
                if dist<want:
                    f=(want-dist)/want
                    fx+=(dx/dist)*f; fy+=(dy/dist)*f
            if fx or fy:
                x1+=fx*step; y1+=fy*step; P[a]=[x1,y1]; moved+=1
        if moved==0: break
    return {n:(float(x),float(y)) for n,(x,y) in P.items()}

def _radial_push(G: nx.Graph, pos: dict[str,tuple[float,float]], role="chronik", alpha=0.6):
    if not pos: return pos
    cx=sum(x for x,_ in pos.values())/len(pos)
    cy=sum(y for _,y in pos.values())/len(pos)
    degs={n:weighted_degree(G,n) for n,d in G.nodes(data=True) if d.get("role")==role}
    m=max(degs.values()) or 1.0
    out=dict(pos)
    for n,(x,y) in pos.items():
        if G.nodes[n].get("role")!=role: continue
        dx=x-cx; dy=y-cy; scale=1.0+alpha*(degs.get(n,0.0)/m)
        out[n]=(cx+dx*scale, cy+dy*scale)
    return out

def compute_layout(G: nx.Graph, mode: str, p: LayoutParams) -> dict[str,tuple[float,float]]:
    if G.number_of_nodes()==0: return {}
    if mode=="bipartite":
        pos0=_bipartite(G,p,scale=2.0)
        pos=_relax(G,pos0,iters=160,base=0.42,step=0.06)
        debug(f"[layout] bipartite done: nodes={len(pos)}")
        return pos
    H=_similarity_graph(G,p)
    if mode=="kamada_kawai":
        pos0=nx.kamada_kawai_layout(H,weight="weight")
    elif mode=="forceatlas2":
        init=_bipartite(G,p,scale=1.2)
        pos0=_forceatlas2(H,init,iters=380,gravity=0.06,scaling=1.2,dt=0.08)
    else:
        pos0=nx.spring_layout(H,weight="weight",iterations=350,seed=42)
    pos=_relax(G,{str(n):(float(x),float(y)) for n,(x,y) in pos0.items()},iters=140,base=0.36,step=0.055)
    pos=_radial_push(G,pos,role="chronik",alpha=0.6)
    debug(f"[layout] {mode} done: nodes={len(pos)}")
    return pos

