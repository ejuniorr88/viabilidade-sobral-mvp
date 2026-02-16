import streamlit as st
from streamlit_folium import st_folium
import folium

st.set_page_config(page_title="Viabilidade Sobral - MVP", layout="wide")

st.title("Viabilidade Sobral (MVP)")
st.write("Clique no mapa para capturar a coordenada do lote.")

# Centro aproximado de Sobral
m = folium.Map(location=[-3.689, -40.348], zoom_start=13)

data = st_folium(m, width=1100, height=600)

if data and data.get("last_clicked"):
    lat = data["last_clicked"]["lat"]
    lng = data["last_clicked"]["lng"]
    st.success(f"Coordenada selecionada: {lat:.6f}, {lng:.6f}")
