import json
from pathlib import Path

import streamlit as st
import folium
from streamlit_folium import st_folium

from shapely.geometry import shape, Point
from shapely.prepared import prep
from shapely.ops import transform
from pyproj import Transformer


# =============================
# Config
# =============================
st.set_page_config(layout="wide", page_title="Mapa de Zoneamento - Sobral")
st.title("Mapa de Zoneamento - Sobral")

DATA_DIR = Path("data")
ZONE_FILE = DATA_DIR / "zoneamento.json"
RUAS_FILE = DATA_DIR / "ruas.json"

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
    return {
        "fillColor": color_for_zone(sigla),
        "color": "#222222",
        "weight": 1,
        "fillOpacity": 0.35,
    }


def ruas_style(_feat):
    return {"color": "#ff4d4d", "weight": 2, "opacity": 0.9}


def ensure_properties_keys(geojson: dict, keys: list[str]) -> dict:
    """Evita AssertionError do folium tooltip: garante que todo mundo tenha as chaves."""
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
    # HTML simples (sem depender de libs)
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


@st.cache_resource(show_spinner=False)
def build_ruas_index(ruas_geojson: dict):
    out = []
    features = (ruas_geojson or {}).get("features") or []
    for feat in features:
        geom = feat.get("geometry")
        props = feat.get("properties") or {}
        if not geom:
            continue
        try:
            shp = shape(geom)
            out.append((shp, props))
        except Exception:
            continue
    return out


def find_nearest_street(ruas_index, lat: float, lon: float, max_dist_m: float = 80.0):
    p = Point(lon, lat)
    p_m = transform(to_3857, p)

    best_props = None
    best_d = 10**18

    for line, props in ruas_index:
        try:
            line_m = transform(to_3857, line)
            d = p_m.distance(line_m)  # metros
            if d < best_d:
                best_d = d
                best_props = props
        except Exception:
            continue

    if best_props is not None and best_d <= max_dist_m:
        return best_props, best_d

    return None, None


# =============================
# Carregar dados
# =============================
if not ZONE_FILE.exists():
    st.error(f"Arquivo não encontrado: {ZONE_FILE}")
    st.stop()

zoneamento = load_geojson(ZONE_FILE)

ruas = None
if RUAS_FILE.exists():
    ruas = load_geojson(RUAS_FILE)

zone_fields = ["sigla", "zona", "zona_sigla", "nome", "NOME", "SIGLA", "name"]
zoneamento = ensure_properties_keys(zoneamento, zone_fields)

zone_index = build_zone_index(zoneamento)
ruas_index = build_ruas_index(ruas) if ruas else None


# =============================
# Layout: mapa + painel
# =============================
col_map, col_panel = st.columns([3, 1], gap="large")

# --- Primeiro: captura do clique (state) ---
# A gente mantém o último clique numa sessão para continuar mostrando o pin/popup
if "click" not in st.session_state:
    st.session_state["click"] = None

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
            interactive=False,
        ).add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)

    # Renderiza uma vez para capturar clique
    out = st_folium(m, width=1200, height=700)

    # Atualiza clique (session)
    last = (out or {}).get("last_clicked")
    if last:
        st.session_state["click"] = {"lat": float(last["lat"]), "lng": float(last["lng"])}

# --- Agora: com o clique salvo, recria o mapa COM pin + popup ---
with col_map:
    click = st.session_state.get("click")

    # Se não tem clique ainda, não precisa redesenhar (evita flicker)
    if click:
        m2 = folium.Map(location=[-3.69, -40.35], zoom_start=13, tiles="OpenStreetMap")

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
        ).add_to(m2)

        if ruas:
            folium.GeoJson(
                ruas,
                name="Ruas",
                style_function=ruas_style,
                interactive=False,
            ).add_to(m2)

        folium.LayerControl(collapsed=False).add_to(m2)

        lat = click["lat"]
        lon = click["lng"]

        # Busca zona/rua para montar o popup
        props_zone = find_zone_for_click(zone_index, lat, lon)

        zona_sigla = ""
        zona_nome = ""
        if props_zone:
            zona_sigla = get_prop(props_zone, "sigla", "SIGLA", "zona_sigla", "ZONA_SIGLA", "name")
            zona_nome = get_prop(props_zone, "zona", "ZONA", "nome", "NOME")

        rua_nome = ""
        hierarquia = ""
        dist_m = None
        if ruas_index:
            props_rua, dist_m = find_nearest_street(ruas_index, lat, lon, max_dist_m=80.0)
            if props_rua:
                rua_nome = get_prop(props_rua, "log_ofic", "LOG_OFIC", "name", "NOME")
                hierarquia = get_prop(props_rua, "hierarquia", "HIERARQUIA")

        # ✅ PIN + POPUP (zona + rua + hierarquia)
        popup_html = html_popup(zona_sigla, zona_nome, rua_nome, hierarquia, dist_m)
        folium.Marker(
            location=[lat, lon],
            tooltip="Ponto selecionado",
            popup=folium.Popup(popup_html, max_width=380),
            icon=folium.Icon(color="blue", icon="info-sign"),
        ).add_to(m2)

        # Centraliza no clique
        m2.location = [lat, lon]
        m2.zoom_start = 16

        st_folium(m2, width=1200, height=700, key="map_with_pin")


with col_panel:
    st.subheader("Consulta por clique")

    click = st.session_state.get("click")
    if not click:
        st.info("Clique em qualquer ponto no mapa para ver a zona e a rua aqui.")
        st.stop()

    lat = float(click["lat"])
    lon = float(click["lng"])

    st.write("**Coordenadas clicadas**")
    st.code(f"lat: {lat:.6f}\nlon: {lon:.6f}", language="text")

    # ===== Zona =====
    props_zone = find_zone_for_click(zone_index, lat, lon)
    if props_zone:
        sigla = get_prop(props_zone, "sigla", "SIGLA", "zona_sigla", "ZONA_SIGLA", "name")
        zona = get_prop(props_zone, "zona", "ZONA", "nome", "NOME")
        st.success("Zona encontrada ✅")
        st.write("**Sigla:**", sigla if sigla else "—")
        st.write("**Zona:**", zona if zona else "—")
    else:
        st.warning("Não encontrei uma zona para esse ponto (talvez fora do zoneamento).")

    st.divider()

    # ===== Rua mais próxima =====
    if ruas_index:
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
    else:
        st.write("**Ruas:** ruas.json não encontrado.")

    with st.expander("Ver properties completas (debug)"):
        st.write("Zoneamento:")
        st.json(props_zone or {})
        st.write("Rua:")
        if ruas_index:
            st.json(props_rua or {})
        else:
            st.json({})
