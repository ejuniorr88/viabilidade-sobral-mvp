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
_to_3857 = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True).transform


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


@st.cache_data(show_spinner=False)
def load_geojson(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


@st.cache_resource(show_spinner=False)
def ensure_properties_keys_cached(zoneamento: dict, keys: tuple[str, ...]) -> dict:
    z = json.loads(json.dumps(zoneamento))
    feats = (z or {}).get("features") or []
    for feat in feats:
        props = feat.get("properties")
        if props is None:
            props = {}
            feat["properties"] = props
        for k in keys:
            if k not in props or props[k] is None:
                props[k] = ""
    return z


def _tree_returns_indices(res) -> bool:
    if res is None:
        return False
    try:
        if len(res) == 0:
            return True
        _ = int(res[0])
        return True
    except Exception:
        return False


@st.cache_resource(show_spinner=False)
def build_zone_index(zone_geojson: dict):
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
    return {"geoms": geoms, "preps": preps_list, "props": props_list, "tree": tree}


def find_zone_for_click(zone_index, lat: float, lon: float):
    p = Point(lon, lat)
    tree = zone_index["tree"]
    if not tree:
        return None

    candidates = tree.query(p)

    geoms = zone_index["geoms"]
    preps_list = zone_index["preps"]
    props_list = zone_index["props"]
    n = len(geoms)

    if _tree_returns_indices(candidates):
        for raw in candidates:
            try:
                i = int(raw)
                if i < 0 or i >= n:
                    continue
                if preps_list[i].contains(p) or geoms[i].intersects(p):
                    return props_list[i]
            except Exception:
                continue
        return None

    for g in candidates:
        try:
            i = geoms.index(g)
            if preps_list[i].contains(p) or geoms[i].intersects(p):
                return props_list[i]
        except Exception:
            continue

    return None


@st.cache_resource(show_spinner=False)
def build_ruas_index(ruas_geojson: dict):
    geoms_m, props_list = [], []
    for feat in (ruas_geojson or {}).get("features") or []:
        geom = feat.get("geometry")
        props = feat.get("properties") or {}
        if not geom:
            continue
        try:
            g = shape(geom)
            g_m = transform(_to_3857, g)
            geoms_m.append(g_m)
            props_list.append(props)
        except Exception:
            continue

    tree = STRtree(geoms_m) if geoms_m else None
    return {"geoms_m": geoms_m, "props": props_list, "tree": tree}


def find_nearest_street(ruas_index, lat: float, lon: float, max_dist_m: float = 80.0):
    if not ruas_index or not ruas_index["tree"]:
        return None

    p_m = transform(_to_3857, Point(lon, lat))
    tree = ruas_index["tree"]
    geoms_m = ruas_index["geoms_m"]
    props_list = ruas_index["props"]
    n = len(geoms_m)

    try:
        nearest = tree.nearest(p_m)
        if nearest is None:
            return None

        # índice
        try:
            i = int(nearest)
            if 0 <= i < n:
                d = p_m.distance(geoms_m[i])
                if d <= max_dist_m:
                    return props_list[i]
                return None
        except Exception:
            pass

        # geometria
        g = nearest
        d = p_m.distance(g)
        if d > max_dist_m:
            return None
        try:
            i = geoms_m.index(g)
            return props_list[i]
        except Exception:
            return None

    except Exception:
        return None


def html_popup(zona_sigla: str, zona_nome: str, rua_nome: str, hierarquia: str):
    return f"""
    <div style="font-family: Arial, sans-serif; font-size: 13px; line-height: 1.35; min-width:260px;">
      <div style="font-weight:700; font-size:14px; margin-bottom:6px;">Consulta do ponto</div>
      <div><b>Zona:</b> {zona_nome or "—"}</div>
      <div><b>Sigla:</b> {zona_sigla or "—"}</div>
      <hr style="margin:8px 0;" />
      <div><b>Rua:</b> {rua_nome or "—"}</div>
      <div><b>Hierarquia:</b> {hierarquia or "—"}</div>
    </div>
    """


# =============================
# Dados
# =============================
if not ZONE_FILE.exists():
    st.error(f"Arquivo não encontrado: {ZONE_FILE}")
    st.stop()

zoneamento_raw = load_geojson(ZONE_FILE)
ruas = load_geojson(RUAS_FILE) if RUAS_FILE.exists() else None

zone_fields = ("sigla", "zona", "zona_sigla", "nome", "NOME", "SIGLA", "name")
zoneamento = ensure_properties_keys_cached(zoneamento_raw, zone_fields)

zone_index = build_zone_index(zoneamento)
ruas_index = build_ruas_index(ruas) if ruas else None


# =============================
# Session state
# =============================
if "click" not in st.session_state:
    st.session_state["click"] = None  # {"lat":..., "lng":...}
if "result" not in st.session_state:
    st.session_state["result"] = None  # dict com zona/rua/hierarquia
if "show_popup" not in st.session_state:
    st.session_state["show_popup"] = False


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
            fields=list(zone_fields),
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
            interactive=False,
        ).add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)

    click = st.session_state["click"]
    result = st.session_state["result"]

    # PIN sempre aparece se houver clique
    if click:
        lat = float(click["lat"])
        lon = float(click["lng"])

        if result and st.session_state["show_popup"]:
            popup_html = html_popup(
                result.get("zona_sigla", ""),
                result.get("zona_nome", ""),
                result.get("rua_nome", ""),
                result.get("hierarquia", ""),
            )
            folium.Marker(
                location=[lat, lon],
                tooltip="Ponto selecionado",
                popup=folium.Popup(popup_html, max_width=420, show=True),
                icon=folium.Icon(color="blue", icon="info-sign"),
            ).add_to(m)
        else:
            folium.Marker(
                location=[lat, lon],
                tooltip="Ponto selecionado",
                icon=folium.Icon(color="blue", icon="info-sign"),
            ).add_to(m)

    out = st_folium(m, width=1200, height=700, key="main_map")

    # ✅ Clique: só salva e rerun (pra desenhar o pin imediatamente)
    last = (out or {}).get("last_clicked")
    if last:
        new_click = {"lat": float(last["lat"]), "lng": float(last["lng"])}
        if st.session_state["click"] != new_click:
            st.session_state["click"] = new_click
            st.session_state["result"] = None
            st.session_state["show_popup"] = False
            st.rerun()


with col_panel:
    st.subheader("Consulta por clique")

    click = st.session_state["click"]
    if not click:
        st.info("Clique em qualquer ponto no mapa. Depois clique em **Ver resultado**.")
        st.stop()

    lat = float(click["lat"])
    lon = float(click["lng"])

    st.write("**Coordenadas clicadas**")
    st.code(f"lat: {lat:.6f}\nlon: {lon:.6f}", language="text")

    # Botão que dispara consulta pesada
    if st.button("🔎 Ver resultado", use_container_width=True):
        props_zone = find_zone_for_click(zone_index, lat, lon)
        zona_sigla = get_prop(props_zone or {}, "sigla", "SIGLA", "zona_sigla", "ZONA_SIGLA", "name")
        zona_nome = get_prop(props_zone or {}, "zona", "ZONA", "nome", "NOME")

        props_rua = find_nearest_street(ruas_index, lat, lon, max_dist_m=80.0)
        rua_nome = get_prop(props_rua or {}, "log_ofic", "LOG_OFIC", "name", "NOME")
        hierarquia = get_prop(props_rua or {}, "hierarquia", "HIERARQUIA")

        st.session_state["result"] = {
            "zona_sigla": zona_sigla,
            "zona_nome": zona_nome,
            "rua_nome": rua_nome,
            "hierarquia": hierarquia,
        }
        st.session_state["show_popup"] = True
        st.rerun()

    result = st.session_state["result"]
    if not result:
        st.caption("Clique em **Ver resultado** para carregar zona/rua/hierarquia.")
        st.stop()

    # Zona
    if result.get("zona_nome") or result.get("zona_sigla"):
        st.success("Zona encontrada ✅")
        st.write("**Sigla:**", result.get("zona_sigla") or "—")
        st.write("**Zona:**", result.get("zona_nome") or "—")
    else:
        st.warning("Não encontrei uma zona para esse ponto.")

    st.divider()

    # Rua
    if result.get("rua_nome") or result.get("hierarquia"):
        st.info("Rua mais próxima 🛣️")
        st.write("**Logradouro:**", result.get("rua_nome") or "—")
        if result.get("hierarquia"):
            st.write("**Hierarquia:**", result.get("hierarquia") or "—")
    else:
        st.write("**Rua mais próxima:** não encontrada (muito longe do clique).")

    with st.expander("Ver resultado bruto (debug)"):
        st.json(result)
