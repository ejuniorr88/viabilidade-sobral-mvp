import streamlit as st
import json
import folium
from streamlit_folium import st_folium

st.set_page_config(layout="wide")

st.title("Mapa de Zoneamento - Sobral")

# Carregar GeoJSONs
with open("data/zoneamento.json", "r", encoding="utf-8") as f:
    zoneamento = json.load(f)

with open("data/ruas.json", "r", encoding="utf-8") as f:
    ruas = json.load(f)

# Criar mapa
m = folium.Map(location=[-3.69, -40.35], zoom_start=13)

# Camada de zoneamento
folium.GeoJson(
    zoneamento,
    name="Zoneamento",
    style_function=lambda x: {
        "fillColor": "#3388ff",
        "color": "#000000",
        "weight": 1,
        "fillOpacity": 0.4,
    },
).add_to(m)

# Camada de ruas
folium.GeoJson(
    ruas,
    name="Ruas",
    style_function=lambda x: {
        "color": "#ff0000",
        "weight": 2,
    },
).add_to(m)

folium.LayerControl().add_to(m)

st_folium(m, width=1200, height=700)
