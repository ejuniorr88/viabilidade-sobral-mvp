import os
import json
from pathlib import Path
from typing import Optional, Dict, Any

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


def fmt_pct(x: Optional[float]) -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x)*100:.0f}%"
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


# =============================
# GeoJSON load / indexes
# =============================
@st.cache_data(show_spinner=False)
def load_geojson(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


@st.cache_resource(show_spinner=False)
def build_zone_index(zone_geojson: dict):
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

    tree = STRtree(geoms) if geoms else None
    geom_id_to_idx = {id(g): i for i, g in enumerate(geoms)}
    return {"geoms": geoms, "preps": preps_list, "props": props_list, "tree": tree, "gid": geom_id_to_idx}


def find_zone_for_click(zone_index, lat: float, lon: float):
    tree = zone_index["tree"]
    if not tree:
        return None

    p = Point(lon, lat)
    candidates = tree.query(p)  # geometrias
    gid = zone_index["gid"]
    geoms = zone_index["geoms"]
    preps_list = zone_index["preps"]
    props_list = zone_index["props"]

    for g in candidates:
        i = gid.get(id(g))
        if i is None:
            continue
        try:
            if preps_list[i].contains(p) or geoms[i].intersects(p):
                return props_list[i]
        except Exception:
            continue
    return None


@st.cache_resource(show_spinner=False)
def build_ruas_index(ruas_geojson: dict):
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

    tree = STRtree(geoms_m) if geoms_m else None
    geom_id_to_idx = {id(g): i for i, g in enumerate(geoms_m)}
    return {"geoms_m": geoms_m, "props": props_list, "tree": tree, "gid": geom_id_to_idx}


def find_nearest_street(ruas_index, lat: float, lon: float, max_dist_m: float = 120.0):
    if not ruas_index or not ruas_index["tree"]:
        return None

    p_m = transform(_to_3857, Point(lon, lat))
    tree = ruas_index["tree"]

    try:
        nearest_geom = tree.nearest(p_m)
        if nearest_geom is None:
            return None

        d = p_m.distance(nearest_geom)
        if d > max_dist_m:
            return None

        i = ruas_index["gid"].get(id(nearest_geom))
        if i is None:
            return None
        return ruas_index["props"][i]
    except Exception:
        return None


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
# Supabase queries
# =============================
@st.cache_data(show_spinner=False, ttl=300)
def sb_list_use_types():
    res = sb.table("use_types").select("code,label,category").eq("is_active", True).order("label").execute()
    return res.data or []


@st.cache_data(show_spinner=False, ttl=300)
def sb_get_zone_rule(zone_sigla: str, use_type_code: str) -> Optional[Dict[str, Any]]:
    if not zone_sigla or not use_type_code:
        return None
    res = (
        sb.table("zone_rules")
        .select("zone_sigla,use_type_code,to_max,tp_min,ia_max,recuo_frontal_m,recuo_lateral_m,recuo_fundos_m,gabarito_m,gabarito_pav,observacoes,source_ref")
        .eq("zone_sigla", zone_sigla)
        .eq("use_type_code", use_type_code)
        .limit(1)
        .execute()
    )
    data = res.data or []
    return data[0] if data else None


@st.cache_data(show_spinner=False, ttl=300)
def sb_get_parking_rule(use_type_code: str) -> Optional[Dict[str, Any]]:
    if not use_type_code:
        return None
    res = (
        sb.table("parking_rules")
        .select("use_type_code,metric,value,min_vagas,source_ref")
        .eq("use_type_code", use_type_code)
        .limit(1)
        .execute()
    )
    data = res.data or []
    return data[0] if data else None


# =============================
# Dados (arquivos)
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
if "calc" not in st.session_state:
    st.session_state["calc"] = None


# =============================
# Layout
# =============================
col_map, col_panel = st.columns([3, 1], gap="large")

with col_map:
    m = folium.Map(location=[-3.69, -40.35], zoom_start=13, tiles="OpenStreetMap")

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

    click = st.session_state["click"]
    if click:
        lat = float(click["lat"])
        lon = float(click["lng"])
        html = popup_html(st.session_state["result"])
        folium.Marker(
            location=[lat, lon],
            tooltip="Ponto selecionado",
            popup=folium.Popup(html, max_width=420, show=True),
            icon=folium.Icon(color="blue", icon="info-sign"),
        ).add_to(m)
        m.location = [lat, lon]
        m.zoom_start = 16

    out = st_folium(m, width=1200, height=700, key="main_map")

    last = (out or {}).get("last_clicked")
    if last:
        new_click = {"lat": float(last["lat"]), "lng": float(last["lng"])}
        if st.session_state["click"] != new_click:
            st.session_state["click"] = new_click
            st.session_state["result"] = None
            st.session_state["calc"] = None
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

    # Botão que faz a consulta de zona/rua (pesado)
    if st.button("🔎 Ver resultado", use_container_width=True):
        with st.spinner("Consultando zona e rua..."):
            st.session_state["result"] = compute_result(zone_index, ruas_index, lat, lon)
        st.session_state["calc"] = None
        st.rerun()

    res = st.session_state["result"]
    if not res:
        st.caption("Clique em **Ver resultado** para carregar zona e rua.")
        st.stop()

    # ===== Resultado base =====
    if res.get("zona_sigla") or res.get("zona_nome"):
        st.success("Zona encontrada ✅")
        st.write("**Sigla:**", res.get("zona_sigla") or "—")
        st.write("**Zona:**", res.get("zona_nome") or "—")
    else:
        st.warning("Não encontrei zona para esse ponto.")

    st.divider()

    if res.get("rua_nome") or res.get("hierarquia"):
        st.info("Rua identificada 🛣️")
        st.write("**Logradouro:**", res.get("rua_nome") or "—")
        st.write("**Hierarquia:**", res.get("hierarquia") or "—")
    else:
        st.warning("Não consegui identificar rua próxima para esse ponto.")

    st.divider()
    st.subheader("Dados do lote/projeto")

    # ===== Uso (do Supabase) =====
    use_types = sb_list_use_types()
    use_options = {u["label"]: u["code"] for u in use_types if u.get("category") == "Residencial"}

    # fallback se não vier nada
    if not use_options:
        use_options = {
            "Residencial Unifamiliar (Casa)": "RES_UNI",
            "Residencial Multifamiliar (Prédio)": "RES_MULTI",
        }

    use_label = st.selectbox("Escolha o uso", list(use_options.keys()))
    use_code = use_options[use_label]

    # ===== Dados do lote =====
    testada = st.number_input("Testada / Frente (m)", min_value=1.0, value=10.0, step=0.5)
    profundidade = st.number_input("Profundidade / Lateral (m)", min_value=1.0, value=30.0, step=0.5)
    esquina = st.checkbox("Lote de esquina")

    # ===== Calcular =====
    if st.button("🧮 Calcular", use_container_width=True):
        zona_sigla = res.get("zona_sigla") or ""
        rule = sb_get_zone_rule(zona_sigla, use_code)
        park = sb_get_parking_rule(use_code)

        area_lote = float(testada) * float(profundidade)

        calc = {
            "area_lote": area_lote,
            "rule": rule,
            "park": park,
            "use_label": use_label,
            "use_code": use_code,
            "zona_sigla": zona_sigla,
            "esquina": bool(esquina),
        }

        # Se tiver regra, calcula m²
        if rule:
            to_max = rule.get("to_max")
            tp_min = rule.get("tp_min")
            ia_max = rule.get("ia_max")

            calc["to_max"] = to_max
            calc["tp_min"] = tp_min
            calc["ia_max"] = ia_max

            calc["area_max_ocupacao_terreo"] = (float(to_max) * area_lote) if to_max is not None else None
            calc["area_min_permeavel"] = (float(tp_min) * area_lote) if tp_min is not None else None
            calc["area_max_total_construida"] = (float(ia_max) * area_lote) if ia_max is not None else None

            # recuos
            calc["recuo_frontal_m"] = rule.get("recuo_frontal_m")
            calc["recuo_lateral_m"] = rule.get("recuo_lateral_m")
            calc["recuo_fundos_m"] = rule.get("recuo_fundos_m")
            calc["gabarito_m"] = rule.get("gabarito_m")
            calc["gabarito_pav"] = rule.get("gabarito_pav")
            calc["observacoes"] = rule.get("observacoes")
            calc["source_ref"] = rule.get("source_ref")

        # vagas (MVP)
        vagas = None
        if park:
            metric = park.get("metric")
            value = park.get("value") or 0
            min_v = park.get("min_vagas")
            if metric == "fixed":
                vagas = int(value)
            elif metric == "per_unit":
                # sem "unidades" ainda no MVP -> assume 1 unidade p/ RES_UNI e "—" p/ multi (ajustar depois)
                if use_code == "RES_UNI":
                    vagas = max(int(value), int(min_v or 0))
                else:
                    vagas = None
            elif metric == "per_area":
                # value = vagas por m² (ex 1/50 = 0.02)
                vagas = int((area_lote * float(value)) // 1)
                if min_v is not None:
                    vagas = max(vagas, int(min_v))
        calc["vagas_min"] = vagas

        st.session_state["calc"] = calc
        st.rerun()

    calc = st.session_state.get("calc")
    if not calc:
        st.caption("Preencha os dados e clique em **Calcular**.")
        st.stop()

    st.divider()
    st.subheader("Resultado urbanístico")

    st.write("**Uso:**", calc.get("use_label"))
    st.write("**Área do lote:**", fmt_m2(calc.get("area_lote")))

    rule = calc.get("rule")
    if not rule:
        st.warning(f"Sem regra cadastrada no Supabase para **{calc.get('zona_sigla')} + {calc.get('use_code')}**.")
        st.caption("Cadastre em `zone_rules` (TO/TP/IA/recuos) e tente novamente.")
        st.stop()

    st.write("**TO máx:**", fmt_pct(calc.get("to_max")))
    st.write("**Área máx. de ocupação (térreo):**", fmt_m2(calc.get("area_max_ocupacao_terreo")))

    st.write("**TP mín:**", fmt_pct(calc.get("tp_min")))
    st.write("**Área mín. permeável:**", fmt_m2(calc.get("area_min_permeavel")))

    st.write("**IA máx:**", calc.get("ia_max") if calc.get("ia_max") is not None else "—")
    st.write("**Área máx. construída total:**", fmt_m2(calc.get("area_max_total_construida")))

    st.divider()
    st.subheader("Recuos / Gabarito")

    st.write("**Recuo frontal:**", fmt_m(calc.get("recuo_frontal_m")))
    st.write("**Recuo lateral:**", fmt_m(calc.get("recuo_lateral_m")))
    st.write("**Recuo fundos:**", fmt_m(calc.get("recuo_fundos_m")))

    if calc.get("gabarito_m") is not None or calc.get("gabarito_pav") is not None:
        st.write("**Gabarito (m):**", fmt_m(calc.get("gabarito_m")))
        st.write("**Gabarito (pav):**", calc.get("gabarito_pav") or "—")

    if calc.get("vagas_min") is not None:
        st.divider()
        st.subheader("Vagas mínimas")
        st.write("**Vagas mín.:**", int(calc.get("vagas_min")))

    if calc.get("observacoes"):
        st.divider()
        st.subheader("Observações")
        st.write(calc.get("observacoes"))

    if calc.get("source_ref"):
        st.caption(f"Fonte: {calc.get('source_ref')}")

    with st.expander("Debug (raw)"):
        st.write("rule:")
        st.json(calc.get("rule") or {})
        st.write("parking:")
        st.json(calc.get("park") or {})
