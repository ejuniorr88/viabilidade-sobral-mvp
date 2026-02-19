import json
from pathlib import Path

import streamlit as st
import folium
from streamlit_folium import st_folium

from shapely.geometry import shape, Point
from shapely.ops import transform
from shapely.prepared import prep
from pyproj import Transformer


# =============================
# Config
# =============================
st.set_page_config(layout="wide", page_title="Viabilidade")
st.title("Viabilidade")

DATA_DIR = Path("data")
ZONE_FILE = DATA_DIR / "zoneamento_light.json"
RUAS_FILE = DATA_DIR / "ruas.json"

# WGS84 -> WebMercator (metros) (só para proximidade de ruas)
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
    return {"fillColor": color_for_zone(sigla), "color": "#222222", "weight": 1, "fillOpacity": 0.30}


def ensure_properties_keys(geojson: dict, keys: tuple[str, ...]) -> dict:
    """Evita erro de tooltip do folium (garante que todos tenham as chaves)."""
    z = json.loads(json.dumps(geojson))  # cópia segura
    feats = (z or {}).get("features") or []
    for feat in feats:
        props = feat.get("properties") or {}
        feat["properties"] = props
        for k in keys:
            if k not in props or props[k] is None:
                props[k] = ""
    return z


def popup_html(result: dict | None):
    # Placeholder até clicar em "Ver resultado"
    if not result:
        return """
        <div style="font-family: Arial, sans-serif; font-size: 13px; line-height: 1.35; min-width:260px;">
          <div style="font-weight:700; font-size:14px; margin-bottom:6px;">Ponto selecionado</div>
          <div style="color:#666;">Clique em <b>Ver resultado</b> para consultar zona e rua.</div>
        </div>
        """

    zona_nome = result.get("zona_nome") or "—"
    zona_sigla = result.get("zona_sigla") or "—"
    rua_nome = result.get("rua_nome") or "—"
    hierarquia = result.get("hierarquia") or "—"

    return f"""
    <div style="font-family: Arial, sans-serif; font-size: 13px; line-height: 1.35; min-width:260px;">
      <div style="font-weight:700; font-size:14px; margin-bottom:6px;">Consulta do ponto</div>
      <div><b>Zona:</b> {zona_nome}</div>
      <div><b>Sigla:</b> {zona_sigla}</div>
      <hr style="margin:8px 0;" />
      <div><b>Rua:</b> {rua_nome}</div>
      <div><b>Hierarquia:</b> {hierarquia}</div>
    </div>
    """


@st.cache_data(show_spinner=False)
def load_geojson(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


@st.cache_resource(show_spinner=False)
def build_zone_index(zone_geojson: dict):
    """
    Índice estável (SEM STRtree):
    - lista de geometrias + prepared para contains rápido
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
    return {"geoms": geoms, "preps": preps_list, "props": props_list}


def find_zone_for_click(zone_index, lat: float, lon: float):
    """Busca zona por loop (estável e suficiente para poucas zonas)."""
    p = Point(lon, lat)
    geoms = zone_index["geoms"]
    preps_list = zone_index["preps"]
    props_list = zone_index["props"]

    for i in range(len(geoms)):
        try:
            if preps_list[i].contains(p) or geoms[i].intersects(p):
                return props_list[i]
        except Exception:
            continue
    return None


@st.cache_resource(show_spinner=False)
def build_ruas_index(ruas_geojson: dict):
    """
    Pré-processa ruas:
    - projeta para 3857 UMA vez
    - guarda geometria em metros + props
    """
    geoms_m, props_list = [], []
    for feat in (ruas_geojson or {}).get("features") or []:
        geom = feat.get("geometry")
        props = feat.get("properties") or {}
        if not geom:
            continue
        try:
            g = shape(geom)                 # WGS84
            g_m = transform(_to_3857, g)     # metros
            geoms_m.append(g_m)
            props_list.append(props)
        except Exception:
            continue
    return {"geoms_m": geoms_m, "props": props_list}


def find_nearest_street(ruas_index, lat: float, lon: float, max_dist_m: float = 120.0):
    """Rua mais próxima por loop (rodando só ao clicar no botão)."""
    if not ruas_index or not ruas_index["geoms_m"]:
        return None

    p_m = transform(_to_3857, Point(lon, lat))
    geoms_m = ruas_index["geoms_m"]
    props_list = ruas_index["props"]

    best_i = None
    best_d = float("inf")

    # Loop simples e estável
    for i, g in enumerate(geoms_m):
        try:
            d = p_m.distance(g)
            if d < best_d:
                best_d = d
                best_i = i
        except Exception:
            continue

    if best_i is None or best_d > max_dist_m:
        return None

    return props_list[best_i]


def compute_result(zone_index, ruas_index, lat: float, lon: float):
    props_zone = find_zone_for_click(zone_index, lat, lon)
    props_rua = find_nearest_street(ruas_index, lat, lon) if ruas_index else None

    zona_sigla = get_prop(props_zone or {}, "sigla", "SIGLA", "zona_sigla", "ZONA_SIGLA", "name")
    zona_nome = get_prop(props_zone or {}, "zona", "ZONA", "nome", "NOME")

    rua_nome = get_prop(props_rua or {}, "log_ofic", "LOG_OFIC", "name", "NOME")
    hierarquia = get_prop(props_rua or {}, "hierarquia", "HIERARQUIA")

    return {
        "zona_sigla": zona_sigla,
        "zona_nome": zona_nome,
        "rua_nome": rua_nome,
        "hierarquia": hierarquia,
        "raw_zone": props_zone or {},
        "raw_rua": props_rua or {},
    }


# =============================
# Dados
# =============================
if not ZONE_FILE.exists():
    st.error(f"Arquivo não encontrado: {ZONE_FILE}")
    st.stop()

zoneamento_raw = load_geojson(ZONE_FILE)
ruas_raw = load_geojson(RUAS_FILE) if RUAS_FILE.exists() else None

zone_fields = ("sigla", "zona", "zona_sigla", "nome", "NOME", "SIGLA", "name")
zoneamento = ensure_properties_keys(zoneamento_raw, zone_fields)

zone_index = build_zone_index(zoneamento)
ruas_index = build_ruas_index(ruas_raw) if ruas_raw else None


# =============================
# Session state
# =============================
if "click" not in st.session_state:
    st.session_state["click"] = None

if "result" not in st.session_state:
    st.session_state["result"] = None


# =============================
# Layout
# =============================
col_map, col_panel = st.columns([3, 1], gap="large")

with col_map:
    m = folium.Map(location=[-3.69, -40.35], zoom_start=13, tiles="OpenStreetMap")

    # Zoneamento (visual)
    zone_aliases = ["Sigla: ", "Zona: ", "Sigla Zona: ", "Nome: ", "Nome: ", "Sigla: ", "Nome: "]
    folium.GeoJson(
        zoneamento,
        name="Zoneamento",
        style_function=zone_style,
        highlight_function=lambda x: {"weight": 3, "color": "#000000", "fillOpacity": 0.40},
        tooltip=folium.GeoJsonTooltip(
            fields=list(zone_fields),
            aliases=zone_aliases,
            sticky=True,
            labels=True,
        ),
    ).add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)

    # PIN sempre que tiver clique salvo (instantâneo)
    click = st.session_state["click"]
    if click:
        lat = float(click["lat"])
        lon = float(click["lng"])

        # Popup: placeholder até consultar
        html = popup_html(st.session_state["result"])
        folium.Marker(
            location=[lat, lon],
            tooltip="Ponto selecionado",
            popup=folium.Popup(html, max_width=420, show=True),
            icon=folium.Icon(color="blue", icon="info-sign"),
        ).add_to(m)

        # Centraliza no clique
        m.location = [lat, lon]
        m.zoom_start = 16

    # Render do mapa (captura clique)
    out = st_folium(m, width=1200, height=700, key="main_map")

    last = (out or {}).get("last_clicked")
    if last:
        new_click = {"lat": float(last["lat"]), "lng": float(last["lng"])}

        # Se mudou o ponto, limpa o resultado (obriga botão novamente)
        if st.session_state["click"] != new_click:
            st.session_state["click"] = new_click
            st.session_state["result"] = None
            st.rerun()


with col_panel:
    st.subheader("Consulta por clique")

    click = st.session_state["click"]
    if not click:
        st.info("Clique no mapa para marcar um ponto.")
        st.stop()

    lat = float(click["lat"])
    lon = float(click["lng"])

    st.write("**Coordenadas clicadas**")
    st.code(f"lat: {lat:.6f}\nlon: {lon:.6f}", language="text")

    # Botão que faz a consulta (só aqui roda o pesado)
    if st.button("🔎 Ver resultado", use_container_width=True):
        with st.spinner("Consultando zona e rua..."):
            st.session_state["result"] = compute_result(zone_index, ruas_index, lat, lon)
        st.rerun()

    res = st.session_state["result"]

    if not res:
        st.caption("Clique em **Ver resultado** para carregar zona e rua.")
        st.stop()

    # ===== Zona =====
    if res.get("zona_sigla") or res.get("zona_nome"):
        st.success("Zona encontrada ✅")
        st.write("**Sigla:**", res.get("zona_sigla") or "—")
        st.write("**Zona:**", res.get("zona_nome") or "—")
    else:
        st.warning("Não encontrei zona para esse ponto.")

    st.divider()

    # ===== Rua =====
    if res.get("rua_nome") or res.get("hierarquia"):
        st.info("Rua identificada 🛣️")
        st.write("**Logradouro:**", res.get("rua_nome") or "—")
        st.write("**Hierarquia:**", res.get("hierarquia") or "—")
    else:
        st.warning("Não consegui identificar rua próxima para esse ponto.")

    with st.expander("Ver properties completas (debug)"):
        st.write("Zoneamento:")
        st.json(res.get("raw_zone") or {})
        st.write("Rua:")
        st.json(res.get("raw_rua") or {})
