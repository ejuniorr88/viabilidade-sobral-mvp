import json
from pathlib import Path

import streamlit as st
import folium
from streamlit_folium import st_folium

from shapely.geometry import shape, Point
from shapely.prepared import prep


# =============================
# Config
# =============================
st.set_page_config(layout="wide", page_title="Mapa de Zoneamento - Sobral")
st.title("Mapa de Zoneamento - Sobral")

DATA_DIR = Path("data")
ZONE_FILE = DATA_DIR / "zoneamento.json"
RUAS_FILE = DATA_DIR / "ruas.json"

# Campos que vamos tentar mostrar (tooltip)
ZONE_FIELDS = ["sigla", "zona", "zona_sigla", "nome", "NOME", "SIGLA"]
ZONE_ALIASES = ["Sigla: ", "Zona: ", "Sigla Zona: ", "Nome: ", "Nome: ", "Sigla: "]


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
    sigla = get_prop(props, "sigla", "SIGLA", "zona_sigla", "ZONA_SIGLA")
    return {
        "fillColor": color_for_zone(sigla),
        "color": "#222222",
        "weight": 1,
        "fillOpacity": 0.35,
    }


def ruas_style(_feat):
    return {"color": "#ff4d4d", "weight": 2, "opacity": 0.9}


def ensure_fields_in_properties(geojson: dict, fields: list[str]) -> dict:
    """
    Evita AssertionError do Folium:
    garante que todas as features tenham todas as chaves do tooltip.
    """
    feats = (geojson or {}).get("features") or []
    for feat in feats:
        props = feat.get("properties")
        if props is None:
            props = {}
            feat["properties"] = props
        for f in fields:
            if f not in props:
                props[f] = ""
    return geojson


@st.cache_data(show_spinner=False)
def load_geojson(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


@st.cache_resource(show_spinner=False)
def build_zone_index(zone_geojson: dict):
    out = []
    features = (zone_geojson or {}).get("features") or []
    for feat in features:
        geom = feat.get("geometry")
        props = feat.get("properties") or {}
        if not geom:
            continue
        try:
            shp = shape(geom)
            out.append((prep(shp), shp, props))
        except Exception:
            continue
    return out


def find_zone_for_click(index, lat: float, lon: float):
    p = Point(lon, lat)
    for prepared, original_geom, props in index:
        try:
            if prepared.contains(p) or original_geom.intersects(p):
                return props
        except Exception:
            continue
    return None


# =============================
# Carregar dados
# =============================
if not ZONE_FILE.exists():
    st.error(f"Arquivo não encontrado: {ZONE_FILE}")
    st.stop()

zoneamento = load_geojson(ZONE_FILE)
zoneamento = ensure_fields_in_properties(zoneamento, ZONE_FIELDS)  # <-- FIX DO ERRO

ruas = None
if RUAS_FILE.exists():
    ruas = load_geojson(RUAS_FILE)

zone_index = build_zone_index(zoneamento)


# =============================
# Layout: mapa + painel
# =============================
col_map, col_panel = st.columns([3, 1], gap="large")

with col_map:
    m = folium.Map(location=[-3.69, -40.35], zoom_start=13, tiles="OpenStreetMap")

    # Zoneamento com tooltip (agora não quebra)
    folium.GeoJson(
        zoneamento,
        name="Zoneamento",
        style_function=zone_style,
        highlight_function=lambda x: {"weight": 3, "color": "#000000", "fillOpacity": 0.45},
        tooltip=folium.GeoJsonTooltip(
            fields=ZONE_FIELDS,
            aliases=ZONE_ALIASES,
            sticky=True,
            labels=True,
        ),
    ).add_to(m)

    # Ruas (não captura clique)
    if ruas:
        folium.GeoJson(
            ruas,
            name="Ruas",
            style_function=ruas_style,
            interactive=False,
        ).add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)

    # Render e captura do clique
    out = st_folium(m, width=1200, height=700)

with col_panel:
    st.subheader("Consulta por clique")

    last = (out or {}).get("last_clicked")
    if not last:
        st.info("Clique em qualquer ponto dentro de Sobral para ver a zona aqui.")
        st.stop()

    lat = last.get("lat")
    lon = last.get("lng")

    st.write("**Coordenadas clicadas**")
    st.code(f"lat: {lat:.6f}\nlon: {lon:.6f}", language="text")

    props = find_zone_for_click(zone_index, lat, lon)

    if not props:
        st.warning("Não encontrei uma zona para esse ponto (talvez esteja fora do zoneamento).")
        st.stop()

    sigla = get_prop(props, "sigla", "SIGLA", "zona_sigla", "ZONA_SIGLA")
    zona = get_prop(props, "zona", "ZONA", "nome", "NOME")
    zona_sigla = get_prop(props, "zona_sigla", "ZONA_SIGLA")

    st.success("Zona encontrada ✅")
    st.write("**Sigla**:", sigla if sigla else "—")
    st.write("**Zona**:", zona if zona else "—")
    if zona_sigla:
        st.write("**Sigla Zona**:", zona_sigla)

    with st.expander("Ver properties completas (debug)"):
        st.json(props)
