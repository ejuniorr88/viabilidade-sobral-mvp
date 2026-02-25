import folium
from streamlit_folium import st_folium

import streamlit as st
st.write("APP CARREGOU ✅")

def render_map(default_center=(-3.689, -40.348), default_zoom=13):
    """
    UI ONLY: renderiza o mapa e devolve clique {lat, lon}.
    (Não faz regra de negócio - SPEC 2.2)
    """
    m = folium.Map(
        location=list(default_center),
        zoom_start=default_zoom,
        control_scale=True,
    )

    # dica visual
    folium.Marker(
        location=list(default_center),
        tooltip="Clique no mapa para selecionar um ponto",
    ).add_to(m)

    out = st_folium(m, width=None, height=560)

    # streamlit-folium retorna coordenadas do último clique
    if out and out.get("last_clicked"):
        return {"lat": out["last_clicked"]["lat"], "lon": out["last_clicked"]["lng"]}

    return None
