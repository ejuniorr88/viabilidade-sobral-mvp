import os
import json
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from numbers import Integral  # ✅ pega int e numpy.int64

import streamlit as st
import folium
from streamlit_folium import st_folium

from shapely.geometry import shape, Point
from shapely.ops import transform
from shapely.prepared import prep
from shapely.strtree import STRtree
from pyproj import Transformer

from supabase import create_client


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
# Style (cards simples)
# =============================
st.markdown(
    """
    <style>
      .card {
        border: 1px solid rgba(49, 51, 63, 0.12);
        border-radius: 14px;
        padding: 14px 16px;
        background: white;
      }
      .card h4 { margin: 0 0 8px 0; font-size: 16px; }
      .muted { color: rgba(49, 51, 63, 0.65); font-size: 13px; }
      .big { font-size: 20px; font-weight: 700; margin: 6px 0 2px 0; }
      .pill {
        display: inline-block;
        padding: 4px 10px;
        border-radius: 999px;
        font-size: 12px;
        background: rgba(0, 174, 239, 0.10);
        color: rgba(0, 95, 130, 1.0);
        margin-bottom: 8px;
      }
      .grid2 {
        display:grid;
        grid-template-columns: 1fr 1fr;
        gap: 12px;
      }
      .warn {
        padding: 10px 12px;
        border-radius: 12px;
        background: rgba(255, 193, 7, 0.18);
        border: 1px solid rgba(255, 193, 7, 0.35);
        margin-top: 10px;
      }
    </style>
    """,
    unsafe_allow_html=True,
)


# =============================
# Supabase
# =============================
@st.cache_resource(show_spinner=False)
def get_supabase():
    url = st.secrets.get("SUPABASE_URL") or os.getenv("SUPABASE_URL")
    key = st.secrets.get("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        return None
    return create_client(url, key)


sb = get_supabase()
if sb is None:
    st.error("Faltam SUPABASE_URL / SUPABASE_ANON_KEY nos Secrets do Streamlit Cloud.")
    st.stop()


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


def ensure_properties_keys(geojson: dict, keys: Tuple[str, ...]) -> dict:
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


def fmt_pct(x: Optional[float]) -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x) * 100:.0f}%"
    except Exception:
        return "—"


def fmt_m(x: Optional[float]) -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x):.2f} m"
    except Exception:
        return "—"


def fmt_m2(x: Optional[float]) -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x):.2f} m²"
    except Exception:
        return "—"


def popup_html(result: dict | None):
    if not result:
        return """
        <div style="font-family: Arial, sans-serif; font-size: 13px; line-height: 1.35; min-width:260px;">
          <div style="font-weight:700; font-size:14px; margin-bottom:6px;">Ponto selecionado</div>
          <div style="color:#666;">Preencha os dados e clique em <b>Calcular</b> para ver zona, rua e índices.</div>
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
      <hr style
