import json
import streamlit as st
import folium
from streamlit_folium import st_folium
from shapely.geometry import shape, Point


st.set_page_config(layout="wide")
st.title("Mapa de Zoneamento - Sobral")

# ===== Carregar GeoJSONs =====
with open("data/zoneamento.json", "r", encoding="utf-8") as f:
    zoneamento = json.load(f)

with open("data/ruas.json", "r", encoding="utf-8") as f:
    ruas = json.load(f)

# ===== Funções auxiliares =====
def get_prop(feat, *keys):
    """Pega a primeira property existente dentre as chaves informadas."""
    props = (feat or {}).get("properties") or {}
    for k in keys:
        if k in props and props[k] not in (None, ""):
            return str(props[k])
    return ""

def color_for_zone(sigla: str) -> str:
    """
    Paleta simples e estável (sem bibliotecas extras).
    Você pode ajustar depois.
    """
    palette = [
        "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
        "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
    ]
    if not sigla:
        return "#3388ff"
    idx = sum(ord(c) for c in sigla) % len(palette)
    return palette[idx]

def zone_style(feat):
    sigla = get_prop(feat, "sigla", "SIGLA", "zona_sigla", "ZONA_SIGLA")
    return {
        "fillColor": color_for_zone(sigla),
        "color": "#222222",
        "weight": 1,
        "fillOpacity": 0.35,
    }

def ruas_style(_feat):
    return {"color": "#ff4d4d", "weight": 2, "opacity": 0.9}

# ===== Criar mapa =====
m = folium.Map(location=[-3.69, -40.35], zoom_start=13, tiles="OpenStreetMap")

# ===== Camada: Zoneamento (tooltip + popup) =====
zone_fields = ["sigla", "zona", "zona_sigla", "nome", "NOME", "SIGLA"]
zone_aliases = ["Sigla: ", "Zona: ", "Sigla Zona: ", "Nome: ", "Nome: ", "Sigla: "]

zone_layer = folium.GeoJson(
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
    popup=folium.GeoJsonPopup(
        fields=zone_fields,
        aliases=zone_aliases,
        labels=True,
        localize=True,
    ),
)
zone_layer.add_to(m)

# ===== Camada: Ruas =====
folium.GeoJson(
    ruas,
    name="Ruas",
    style_function=ruas_style,
    interactive=False,  # ruas não capturam clique
).add_to(m)

folium.LayerControl(collapsed=False).add_to(m)

# =========================
# Painel lateral (clique)
# =========================

@st.cache_data(show_spinner=False)
def build_zone_index(zoneamento_fc):
    """Pré-processa geometrias pra busca rápida."""
    items = []
    for feat in zoneamento_fc.get("features", []):
        geom = feat.get("geometry")
        props = feat.get("properties", {}) or {}
        if geom:
            try:
                items.append((shape(geom), props))
            except Exception:
                pass
    return items

zones_idx = build_zone_index(zoneamento)

def find_zone_props(lat, lon):
    """Retorna as properties da zona que contém o ponto (lon,lat)."""
    p = Point(lon, lat)  # atenção: Point(x=lon, y=lat)
    for poly, props in zones_idx:
        try:
            if poly.contains(p):
                return props
        except Exception:
            continue
    return None

# Render do mapa + captura do clique
out = st_folium(m, width=1200, height=700)

# Pega o clique (algumas versões retornam 'last_clicked')
clicked = out.get("last_clicked") or out.get("last_object_clicked")

st.sidebar.title("Detalhes do ponto")
if clicked and isinstance(clicked, dict) and ("lat" in clicked and "lng" in clicked):
    lat = clicked["lat"]
    lon = clicked["lng"]

    st.sidebar.write(f"📍 Clique: **{lat:.6f}, {lon:.6f}**")

    props = find_zone_props(lat, lon)
    if props:
        # Mostra campos mais comuns primeiro
        sigla = props.get("sigla") or props.get("SIGLA") or props.get("zona_sigla") or props.get("ZONA_SIGLA")
        zona  = props.get("zona")  or props.get("nome")  or props.get("NOME")

        st.sidebar.subheader("Zoneamento")
        st.sidebar.write(f"**Sigla:** {sigla if sigla else '-'}")
        st.sidebar.write(f"**Zona:** {zona if zona else '-'}")

        st.sidebar.divider()
        st.sidebar.subheader("Propriedades (completo)")
        st.sidebar.json(props)
    else:
        st.sidebar.warning("Clique dentro de um polígono de zoneamento para ver os dados.")
else:
    st.sidebar.info("Clique em uma zona no mapa para ver os detalhes aqui.")
