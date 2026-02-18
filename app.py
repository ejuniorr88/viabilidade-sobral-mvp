import json
import streamlit as st
import folium
from streamlit_folium import st_folium

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
    interactive=False,  # <- importante: ruas não "pegam" o clique
).add_to(m)


folium.LayerControl(collapsed=False).add_to(m)

# Render no Streamlit
st_folium(m, width=1200, height=700)
