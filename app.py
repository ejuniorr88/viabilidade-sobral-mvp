import streamlit as st

from ui.map_view import render_map
from domain.urban_calc import GeoEngine

st.set_page_config(layout="wide", page_title="Viabilidade")
st.title("Viabilidade Sobral — dev-architecture v1.1")

# Motor geográfico (carrega zoneamento_light + ruas)
if "geo" not in st.session_state:
    st.session_state.geo = GeoEngine(
        zone_file="data/zoneamento_light.json",
        streets_file="data/ruas.json",
    )

st.subheader("1) Clique no mapa para detectar Zona e Via")

col_map, col_info = st.columns([1.25, 1])

with col_map:
    clicked = render_map(default_center=(-3.689, -40.348), default_zoom=13)

with col_info:
    st.subheader("Resultado (SPEC 3.2)")

    if not clicked:
        st.info("Clique no mapa para capturar latitude/longitude.")
    else:
        lat = clicked["lat"]
        lon = clicked["lon"]

        location = st.session_state.geo.compute_location(lat, lon)

        st.write("**Clique capturado:**")
        st.json({"lat": lat, "lon": lon})

        st.write("**Location (zona + via):**")
        st.json(location)

        with st.expander("Debug (raw_zone / raw_rua)"):
            st.json({
                "raw_zone": location.get("raw_zone"),
                "raw_rua": location.get("raw_rua"),
            })
