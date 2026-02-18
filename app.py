import json
from pathlib import Path

import streamlit as st
import folium
from streamlit_folium import st_folium

from shapely.geometry import shape, Point
from shapely.ops import transform
from shapely.prepared import prep
from shapely.strtree import STRtree
from pyproj import Transformer


# =============================
# Config
# =============================
st.set_page_config(layout="wide", page_title="Mapa de Zoneamento - Sobral")
st.title("Mapa de Zoneamento - Sobral")

DATA_DIR = Path("data")
ZONE_FILE = DATA_DIR / "zoneamento.json"
RUAS_FILE = DATA_DIR / "ruas.json"

# WGS84 -> WebMercator (metros)
to_3857 = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True).transform


# =============================
# Utils
# =============================
def get_prop(props: dict, *keys) -> str:
    props = props or {}
    for k in keys:
        if k in props and props[k] not in (None, ""):
            return str(props[k])
    return ""


def color_for_zone(sigla: str) -> str:
    palette = [
        "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
        "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
    ]
    if not sigla:
        return "#3388ff"
    idx = sum(ord(c) for c in sigla) % len(palette)
    return palette[idx]


def zone_style(feat):
    props = (feat or {}).get("properties") or {}
    sigla = get_prop(props, "sigla", "SIGLA", "zona_sigla", "ZONA_SIGLA", "name")
    return {"fillColor": color_for_zone(sigla), "color": "#222222", "weight": 1, "fillOpacity": 0.35}


def ruas_style(_feat):
    return {"color": "#ff4d4d", "weight": 2, "opacity": 0.9}


def ensure_properties_keys(geojson: dict, keys: list[str]) -> dict:
    feats = (geojson or {}).get("features") or []
    for feat in feats:
        props = feat.get("properties")
        if props is None:
            props = {}
            feat["properties"] = props
        for k in keys:
            if k not in props or props[k] is None:
                props[k] = ""
    return geojson


def html_popup(zona_sigla: str, zona_nome: str, rua_nome: str, hierarquia: str, dist_m: float | None):
    dtxt = f"{dist_m:.1f} m" if dist_m is not None else "—"
    return f"""
    <div style="font-family: Arial, sans-serif; font-size: 13px; line-height: 1.35;">
      <div style="font-weight:700; font-size:14px; margin-bottom:6px;">Consulta do ponto</div>
      <div><b>Zona:</b> {zona_nome or "—"}</div>
      <div><b>Sigla:</b> {zona_sigla or "—"}</div>
      <hr style="margin:8px 0;" />
      <div><b>Rua:</b> {rua_nome or "—"}</div>
      <div><b>Hierarquia:</b> {hierarquia or "—"}</div>
      <div style="color:#666;"><b>Distância aprox.:</b> {dtxt}</div>
    </div>
    """


@st.cache_data(show_spinner=False)
def load_geojson(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


@st.cache_resource(show_spinner=False)
def build_zone_index(zone_geojson: dict):
    """
    Índice espacial para zoneamento (rápido):
    - geoms: lista de polígonos
    - preps: geometria preparada pra contains
    - props: properties
    - tree: STRtree
    """
    geoms, preps_list, props_list = [], [], []
    for feat in (zone_geojson or {}).get("features") or []:
        geom = feat.get("geometry")
        props = feat.get("properties") or {}
        if not geom:
            continue
        try:
            g = shape(geom)
            geoms.append(g)
            preps_list.append(prep(g))
            props_list.append(props)
        except Exception:
            continue

    tree = STRtree(geoms) if geoms else None
    geom_to_idx = {id(g): i for i, g in enumerate(geoms)}
    return {"geoms": geoms, "preps": preps_list, "props": props_list, "tree": tree, "gid": geom_to_idx}


def find_zone_for_click(zone_index, lat: float, lon: float):
    p = Point(lon, lat)
    tree = zone_index["tree"]
    if not tree:
        return None

    # pega candidatos pelo bbox (bem mais rápido que varrer tudo)
    candidates = tree.query(p)
    gid = zone_index["gid"]
    preps_list = zone_index["preps"]
    geoms = zone_index["geoms"]
    props_list = zone_index["props"]

    for g in candidates:
        i = gid.get(id(g))
        if i is None:
            continue
        try:
            if preps_list[i].contains(p) or geoms[i].intersects(p):
                return props_list[i]
        except Exception:
            continue
    return None


@st.cache_resource(show_spinner=False)
def build_ruas_index(ruas_geojson: dict):
    """
    Pré-projeta TODAS as ruas pra 3857 UMA vez e cria STRtree (rápido).
    """
    geoms_m, props_list = [], []
    for feat in (ruas_geojson or {}).get("features") or []:
        geom = feat.get("geometry")
        props = feat.get("properties") or {}
        if not geom:
            continue
        try:
            g = shape(geom)                 # WGS84
            g_m = transform(to_3857, g)      # metros
            geoms_m.append(g_m)
            props_list.append(props)
        except Exception:
            continue

    tree = STRtree(geoms_m) if geoms_m else None
    geom_to_idx = {id(g): i for i, g in enumerate(geoms_m)}
    return {"geoms_m": geoms_m, "props": props_list, "tree": tree, "gid": geom_to_idx}


def find_nearest_street(ruas_index, lat: float, lon: float, max_dist_m: float = 80.0):
    """
    Rua mais próxima usando STRtree.
    """
    if not ruas_index or not ruas_index["tree"]:
        return None, None

    p_m = transform(to_3857, Point(lon, lat))
    tree = ruas_index["tree"]

    # Shapely >=2: nearest é bem rápido
    try:
        nearest_geom = tree.nearest(p_m)
        if nearest_geom is None:
            return None, None
        d = p_m.distance(nearest_geom)
        if d > max_dist_m:
            return None, None
        i = ruas_index["gid"].get(id(nearest_geom))
        return (ruas_index["props"][i] if i is not None else None), d
    except Exception:
        # fallback: query por bbox buffer
        candidates = tree.query(p_m.buffer(max_dist_m))
        best_d = 10**18
        best_props = None
        gid = ruas_index["gid"]
        for g in candidates:
            try:
                d = p_m.distance(g)
                if d < best_d:
                    best_d = d
                    i = gid.get(id(g))
                    best_props = ruas_index["props"][i] if i is not None else None
            except Exception:
                continue
        if best_props is not None and best_d <= max_dist_m:
            return best_props, best_d
        return None, None


# =============================
# Dados
# =============================
if not ZONE_FILE.exists():
    st.error(f"Arquivo não encontrado: {ZONE_FILE}")
    st.stop()

zoneamento = load_geojson(ZONE_FILE)
ruas = load_geojson(RUAS_FILE) if RUAS_FILE.exists() else None

zone_fields = ["sigla", "zona", "zona_sigla", "nome", "NOME", "SIGLA", "name"]
zoneamento = ensure_properties_keys(zoneamento, zone_fields)

zone_index = build_zone_index(zoneamento)
ruas_index = build_ruas_index(ruas) if ruas else None


# =============================
# Session state do clique
# =============================
if "click" not in st.session_state:
    st.session_state["click"] = None


# =============================
# Layout
# =============================
col_map, col_panel = st.columns([3, 1], gap="large")

with col_map:
    m = folium.Map(location=[-3.69, -40.35], zoom_start=13, tiles="OpenStreetMap")

    zone_aliases = ["Sigla: ", "Zona: ", "Sigla Zona: ", "Nome: ", "Nome: ", "Sigla: ", "Nome: "]

    folium.GeoJson(
        zoneamento,
        name="Zoneamento",
        style_function=zone_style,
        highlight_function=lambda x: {"weight": 3, "color": "#000000", "fillOpacity": 0.45},
        tooltip=folium.GeoJsonTooltip(
            fields=zone_fields,
            aliases=zone_aliases,
            sticky=True,
            labels=True,
        ),
    ).add_to(m)

    if ruas:
        folium.GeoJson(
            ruas,
            name="Ruas",
            style_function=ruas_style,
            interactive=False,  # não rouba clique
        ).add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)

    # ===== Se já existe clique, desenha PIN + POPUP (já abre)
    click = st.session_state["click"]
    if click:
        lat = click["lat"]
        lon = click["lng"]

        props_zone = find_zone_for_click(zone_index, lat, lon)
        zona_sigla = get_prop(props_zone or {}, "sigla", "SIGLA", "zona_sigla", "ZONA_SIGLA", "name")
        zona_nome = get_prop(props_zone or {}, "zona", "ZONA", "nome", "NOME")

        props_rua, dist_m = find_nearest_street(ruas_index, lat, lon, max_dist_m=80.0)
        rua_nome = get_prop(props_rua or {}, "log_ofic", "LOG_OFIC", "name", "NOME")
        hierarquia = get_prop(props_rua or {}, "hierarquia", "HIERARQUIA")

        popup_html = html_popup(zona_sigla, zona_nome, rua_nome, hierarquia, dist_m)

        folium.Marker(
            location=[lat, lon],
            tooltip="Ponto selecionado",
            popup=folium.Popup(popup_html, max_width=380, show=True),  # ✅ abre automaticamente
            icon=folium.Icon(color="blue", icon="info-sign"),
        ).add_to(m)

        # opcional: centraliza
        m.location = [lat, lon]
        m.zoom_start = 16

    # ===== Render único do mapa (captura clique)
    out = st_folium(m, width=1200, height=700, key="main_map")

    # Se clicou, salva e reroda (pra desenhar pin/popup imediatamente)
    last = (out or {}).get("last_clicked")
    if last:
        new_click = {"lat": float(last["lat"]), "lng": float(last["lng"])}
        if st.session_state["click"] != new_click:
            st.session_state["click"] = new_click
            st.rerun()


with col_panel:
    st.subheader("Consulta por clique")

    click = st.session_state["click"]
    if not click:
        st.info("Clique em qualquer ponto no mapa para ver a zona e a rua aqui.")
        st.stop()

    lat = float(click["lat"])
    lon = float(click["lng"])

    st.write("**Coordenadas clicadas**")
    st.code(f"lat: {lat:.6f}\nlon: {lon:.6f}", language="text")

    props_zone = find_zone_for_click(zone_index, lat, lon)
    if props_zone:
        sigla = get_prop(props_zone, "sigla", "SIGLA", "zona_sigla", "ZONA_SIGLA", "name")
        zona = get_prop(props_zone, "zona", "ZONA", "nome", "NOME")
        st.success("Zona encontrada ✅")
        st.write("**Sigla:**", sigla if sigla else "—")
        st.write("**Zona:**", zona if zona else "—")
    else:
        st.warning("Não encontrei uma zona para esse ponto.")

    st.divider()

    props_rua, dist_m = find_nearest_street(ruas_index, lat, lon, max_dist_m=80.0)
    if props_rua:
        nome_rua = get_prop(props_rua, "log_ofic", "LOG_OFIC", "name", "NOME")
        hierarquia = get_prop(props_rua, "hierarquia", "HIERARQUIA")
        st.info("Rua mais próxima 🛣️")
        st.write("**Logradouro:**", nome_rua if nome_rua else "—")
        if hierarquia:
            st.write("**Hierarquia:**", hierarquia)
        st.caption(f"Distância aprox.: {dist_m:.1f} m")
    else:
        st.write("**Rua mais próxima:** não encontrada (muito longe do clique).")

    with st.expander("Ver properties completas (debug)"):
        st.write("Zoneamento:")
        st.json(props_zone or {})
        st.write("Rua:")
        st.json(props_rua or {})
