import os
import json
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from numbers import Integral

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
      .warn {
        background: rgba(255, 193, 7, 0.12);
        border: 1px solid rgba(255, 193, 7, 0.35);
        padding: 10px 12px;
        border-radius: 12px;
        margin-top: 10px;
      }
      .ok {
        background: rgba(40, 167, 69, 0.10);
        border: 1px solid rgba(40, 167, 69, 0.25);
        padding: 10px 12px;
        border-radius: 12px;
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
      <hr style="margin:8px 0;" />
      <div><b>Rua:</b> {rua_nome}</div>
      <div><b>Hierarquia:</b> {hierarquia}</div>
    </div>
    """


def envelope_area(
    testada: float,
    profundidade: float,
    rec_fr: float,
    rec_fun: float,
    rec_lat: float,
    esquina: bool,
    corner_two_fronts: bool,
    attach_one_side: bool,
) -> Dict[str, Any]:
    """
    Retorna envelope (miolo) e dimensões úteis.

    Meio de quadra:
      largura_util = testada - (lat_esq + lat_dir)
      prof_util    = profundidade - frontal - fundo

    Esquina (simplificado):
      - se corner_two_fronts=True: considera 2 frentes
      - assume 1 lateral é "frente secundária" (usa rec_fr) e a outra é "lateral interna" (usa rec_lat)
      - attach_one_side só zera a lateral interna (nunca a frente secundária)
    """
    testada = float(testada)
    profundidade = float(profundidade)

    rec_fr = float(rec_fr)
    rec_fun = float(rec_fun)
    rec_lat = float(rec_lat)

    if not esquina:
        lat_internal = rec_lat
        lat_other = rec_lat
        if attach_one_side:
            lat_internal = 0.0

        largura_util = max(testada - (lat_internal + lat_other), 0.0)
        prof_util = max(profundidade - rec_fr - rec_fun, 0.0)
        area = largura_util * prof_util
        return {
            "largura_util": largura_util,
            "prof_util": prof_util,
            "area_miolo": area,
            "esquina_modelo": "meio_quadra",
        }

    # esquina
    if corner_two_fronts:
        lat_internal = rec_lat
        if attach_one_side:
            lat_internal = 0.0

        # largura perde: lateral interna + frente secundária (rec_fr)
        largura_util = max(testada - (lat_internal + rec_fr), 0.0)
        # profundidade perde: frente principal + fundo
        prof_util = max(profundidade - rec_fr - rec_fun, 0.0)

        area = largura_util * prof_util
        return {
            "largura_util": largura_util,
            "prof_util": prof_util,
            "area_miolo": area,
            "esquina_modelo": "esquina_2_frentes",
        }

    # esquina mas sem considerar 2 frentes (vira meio de quadra)
    lat_internal = rec_lat
    lat_other = rec_lat
    if attach_one_side:
        lat_internal = 0.0
    largura_util = max(testada - (lat_internal + lat_other), 0.0)
    prof_util = max(profundidade - rec_fr - rec_fun, 0.0)
    area = largura_util * prof_util
    return {
        "largura_util": largura_util,
        "prof_util": prof_util,
        "area_miolo": area,
        "esquina_modelo": "esquina_sem_2_frentes",
    }


# =============================
# GeoJSON load / indexes
# =============================
@st.cache_data(show_spinner=False)
def load_geojson(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _is_index(x) -> bool:
    return isinstance(x, Integral)


def _tree_returns_indices(res) -> bool:
    if res is None:
        return False
    try:
        if len(res) == 0:
            return True
        return _is_index(res[0])
    except Exception:
        return False


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
    candidates = tree.query(p)
    geoms = zone_index["geoms"]
    preps_list = zone_index["preps"]
    props_list = zone_index["props"]
    gid = zone_index["gid"]

    # Shapely 2: índices
    if _tree_returns_indices(candidates):
        for i in candidates:
            try:
                i = int(i)
                if preps_list[i].contains(p) or geoms[i].intersects(p):
                    return props_list[i]
            except Exception:
                continue
        return None

    # Shapely 1.x: geometrias
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
    geoms_m = ruas_index["geoms_m"]
    props_list = ruas_index["props"]
    gid = ruas_index["gid"]

    try:
        nearest = tree.nearest(p_m)
        if nearest is None:
            return None

        # Shapely 2 pode retornar índice
        if _is_index(nearest):
            i = int(nearest)
            d = p_m.distance(geoms_m[i])
            if d > max_dist_m:
                return None
            return props_list[i]

        # Shapely 1.x retorna geometria
        g = nearest
        d = p_m.distance(g)
        if d > max_dist_m:
            return None
        i = gid.get(id(g))
        if i is None:
            return None
        return props_list[i]
    except Exception:
        return None


def compute_location(zone_index, ruas_index, lat: float, lon: float):
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
    """
    Tenta primeiro um SELECT "completo" (inclui subsolo/area_max/testada_max).
    Se essas colunas ainda não existirem no Supabase, cai para um SELECT "mínimo"
    sem quebrar o app.
    """
    if not zone_sigla or not use_type_code:
        return None

    select_full = (
        "zone_sigla,use_type_code,"
        "to_max,tp_min,ia_min,ia_max,"
        "to_subsolo_max,"  # NOVO
        "recuo_frontal_m,recuo_lateral_m,recuo_fundos_m,"
        "gabarito_m,gabarito_pav,"
        "area_min_lote_m2,area_max_lote_m2,"  # NOVO
        "testada_min_meio_m,testada_min_esquina_m,testada_max_m,"  # NOVO
        "allow_attach_one_side,notes,special_area_tag,"
        "observacoes,source_ref"
    )

    select_min = (
        "zone_sigla,use_type_code,"
        "to_max,tp_min,ia_min,ia_max,"
        "recuo_frontal_m,recuo_lateral_m,recuo_fundos_m,"
        "gabarito_m,gabarito_pav,"
        "area_min_lote_m2,testada_min_meio_m,testada_min_esquina_m,"
        "allow_attach_one_side,notes,special_area_tag,"
        "observacoes,source_ref"
    )

    try:
        res = (
            sb.table("zone_rules")
            .select(select_full)
            .eq("zone_sigla", zone_sigla)
            .eq("use_type_code", use_type_code)
            .limit(1)
            .execute()
        )
    except Exception:
        res = (
            sb.table("zone_rules")
            .select(select_min)
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
# Cálculos urbanísticos
# =============================
def estimate_pavimentos(gabarito_pav: Optional[int], gabarito_m: Optional[float]) -> Optional[int]:
    try:
        if gabarito_pav not in (None, "", 0):
            return int(gabarito_pav)
    except Exception:
        pass

    try:
        if gabarito_m is None:
            return None
        pav = int(float(gabarito_m) // 3.0)
        return max(pav, 1) if pav > 0 else 1
    except Exception:
        return None


def compute_urbanism(
    zone_sigla: str,
    use_label: str,
    use_code: str,
    testada: float,
    profundidade: float,
    esquina: bool,
    corner_two_fronts: bool,
    attach_one_side: bool,
    rule: Optional[Dict[str, Any]],
    park: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    area_lote = float(testada) * float(profundidade)

    calc: Dict[str, Any] = {
        "use_label": use_label,
        "use_code": use_code,
        "zona_sigla": zone_sigla,
        "testada": float(testada),
        "profundidade": float(profundidade),
        "esquina": bool(esquina),
        "corner_two_fronts": bool(corner_two_fronts),
        "attach_one_side": bool(attach_one_side),
        "area_lote": area_lote,
        "rule": rule,
        "park": park,
    }

    if rule:
        to_max = rule.get("to_max")
        tp_min = rule.get("tp_min")
        ia_max = rule.get("ia_max")
        ia_min = rule.get("ia_min")
        to_subsolo_max = rule.get("to_subsolo_max")

        rec_fr = rule.get("recuo_frontal_m")
        rec_lat = rule.get("recuo_lateral_m")
        rec_fun = rule.get("recuo_fundos_m")

        g_m = rule.get("gabarito_m")
        g_pav = rule.get("gabarito_pav")

        calc["to_max"] = to_max
        calc["tp_min"] = tp_min
        calc["ia_min"] = ia_min
        calc["ia_max"] = ia_max
        calc["to_subsolo_max"] = to_subsolo_max

        calc["area_max_ocupacao_to"] = (float(to_max) * area_lote) if to_max is not None else None
        calc["area_min_permeavel"] = (float(tp_min) * area_lote) if tp_min is not None else None
        calc["area_max_total_construida"] = (float(ia_max) * area_lote) if ia_max is not None else None
        calc["area_max_subsolo"] = (float(to_subsolo_max) * area_lote) if to_subsolo_max is not None else None

        calc["recuo_frontal_m"] = rec_fr
        calc["recuo_lateral_m"] = rec_lat
        calc["recuo_fundos_m"] = rec_fun

        calc["gabarito_m"] = g_m
        calc["gabarito_pav"] = g_pav

        calc["area_min_lote_m2"] = rule.get("area_min_lote_m2")
        calc["area_max_lote_m2"] = rule.get("area_max_lote_m2")
        calc["testada_min_meio_m"] = rule.get("testada_min_meio_m")
        calc["testada_min_esquina_m"] = rule.get("testada_min_esquina_m")
        calc["testada_max_m"] = rule.get("testada_max_m")

        calc["allow_attach_one_side"] = bool(rule.get("allow_attach_one_side") or False)
        calc["notes"] = rule.get("notes")
        calc["special_area_tag"] = rule.get("special_area_tag")

        calc["observacoes"] = rule.get("observacoes")
        calc["source_ref"] = rule.get("source_ref")

        # Envelope por recuos (miolo) - considera esquina
        if rec_lat is not None and rec_fr is not None and rec_fun is not None:
            env = envelope_area(
                testada=testada,
                profundidade=profundidade,
                rec_fr=float(rec_fr),
                rec_fun=float(rec_fun),
                rec_lat=float(rec_lat),
                esquina=bool(esquina),
                corner_two_fronts=bool(corner_two_fronts),
                attach_one_side=bool(attach_one_side),
            )
            calc["largura_util_miolo"] = env["largura_util"]
            calc["prof_util_miolo"] = env["prof_util"]
            calc["area_miolo"] = env["area_miolo"]
            calc["esquina_modelo"] = env.get("esquina_modelo")
        else:
            calc["largura_util_miolo"] = None
            calc["prof_util_miolo"] = None
            calc["area_miolo"] = None
            calc["esquina_modelo"] = None

        area_to = calc.get("area_max_ocupacao_to")
        area_miolo = calc.get("area_miolo")
        if area_to is not None and area_miolo is not None:
            calc["area_max_ocupacao_real"] = min(float(area_to), float(area_miolo))
        else:
            calc["area_max_ocupacao_real"] = area_to if area_to is not None else area_miolo

        calc["pavimentos_estimados"] = estimate_pavimentos(g_pav, g_m)

    # vagas (MVP)
    vagas = None
    if park:
        metric = park.get("metric")
        value = park.get("value") or 0
        min_v = park.get("min_vagas")
        if metric == "fixed":
            try:
                vagas = int(value)
            except Exception:
                vagas = None
        elif metric == "per_unit":
            if use_code == "RES_UNI":
                try:
                    vagas = max(int(value), int(min_v or 0))
                except Exception:
                    vagas = None
        elif metric == "per_area":
            try:
                vagas = int((area_lote * float(value)) // 1)
                if min_v is not None:
                    vagas = max(vagas, int(min_v))
            except Exception:
                vagas = None

    calc["vagas_min"] = vagas
    return calc


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
if "res" not in st.session_state:
    st.session_state["res"] = None
if "calc" not in st.session_state:
    st.session_state["calc"] = None
if "last_rule" not in st.session_state:
    st.session_state["last_rule"] = None
if "attach_choice" not in st.session_state:
    st.session_state["attach_choice"] = False


# =============================
# Layout (Mapa + Painel)
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
        html = popup_html(st.session_state["res"])
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
            st.session_state["res"] = None
            st.session_state["calc"] = None
            st.session_state["last_rule"] = None
            st.session_state["attach_choice"] = False
            st.rerun()


with col_panel:
    st.subheader("1) Marque o lote no mapa")

    click = st.session_state["click"]
    if not click:
        st.info("Clique no mapa para marcar um ponto.")
        st.stop()

    lat = float(click["lat"])
    lon = float(click["lng"])
    st.write("**Coordenadas clicadas**")
    st.code(f"lat: {lat:.6f}\nlon: {lon:.6f}", language="text")

    st.subheader("2) Dados do lote/projeto")

    use_types = sb_list_use_types()
    use_options = {u["label"]: u["code"] for u in use_types if u.get("category") == "Residencial"}

    if not use_options:
        use_options = {
            "Residencial Unifamiliar (Casa)": "RES_UNI",
            "Residencial Multifamiliar (Prédio)": "RES_MULTI",
        }

    use_label = st.selectbox("Escolha o uso", list(use_options.keys()))
    use_code = use_options[use_label]

    testada = st.number_input("Testada / Frente (m)", min_value=1.0, value=10.0, step=0.5)
    profundidade = st.number_input("Profundidade / Lateral (m)", min_value=1.0, value=30.0, step=0.5)

    esquina = st.checkbox("Lote de esquina", value=False)

    corner_two_fronts = True
    if esquina:
        corner_two_fronts = st.checkbox("Considerar 2 frentes (esquina)", value=True)

    st.subheader("3) Calcular")

    if st.button("🧮 Calcular", use_container_width=True):
        with st.spinner("Calculando..."):
            res = compute_location(zone_index, ruas_index, lat, lon)
            st.session_state["res"] = res

            zona_sigla = res.get("zona_sigla") or ""
            rule = sb_get_zone_rule(zona_sigla, use_code)
            park = sb_get_parking_rule(use_code)

            st.session_state["last_rule"] = rule

            # se mudou zona/uso, por padrão não encosta (até você marcar)
            # (mantém se quiser: comente a linha abaixo)
            st.session_state["attach_choice"] = False

            calc = compute_urbanism(
                zone_sigla=zona_sigla,
                use_label=use_label,
                use_code=use_code,
                testada=float(testada),
                profundidade=float(profundidade),
                esquina=bool(esquina),
                corner_two_fronts=bool(corner_two_fronts),
                attach_one_side=bool(st.session_state["attach_choice"]),
                rule=rule,
                park=park,
            )
            st.session_state["calc"] = calc

        st.rerun()

    # Checkbox "encostar" (fora do botão) — agora funciona de verdade
    last_rule = st.session_state.get("last_rule") or None
    allow_attach = bool((last_rule or {}).get("allow_attach_one_side") or False)
    if allow_attach:
        new_attach = st.checkbox(
            "Encostar em 1 lateral (zerar recuo)",
            value=bool(st.session_state["attach_choice"]),
            key="attach_choice_checkbox",
        )
        if new_attach != bool(st.session_state["attach_choice"]):
            st.session_state["attach_choice"] = bool(new_attach)
            # recalcula automaticamente se já existe resultado
            if st.session_state.get("res") and st.session_state.get("calc"):
                res = st.session_state["res"]
                zona_sigla = res.get("zona_sigla") or ""
                rule = st.session_state.get("last_rule")
                park = st.session_state.get("calc", {}).get("park")
                calc = compute_urbanism(
                    zone_sigla=zona_sigla,
                    use_label=use_label,
                    use_code=use_code,
                    testada=float(testada),
                    profundidade=float(profundidade),
                    esquina=bool(esquina),
                    corner_two_fronts=bool(corner_two_fronts),
                    attach_one_side=bool(st.session_state["attach_choice"]),
                    rule=rule,
                    park=park,
                )
                st.session_state["calc"] = calc
                st.rerun()
    else:
        st.session_state["attach_choice"] = False

    st.caption("💡 Dica: o pin aparece na hora. O cálculo acontece só quando você clicar em **Calcular**.")


# =============================
# RESULTADOS
# =============================
res = st.session_state.get("res")
calc = st.session_state.get("calc")

st.divider()
st.markdown("## Resultados")

if not res or not calc:
    st.caption("Clique no mapa, preencha os dados e depois clique em **Calcular** para ver os resultados aqui embaixo.")
    st.stop()

c1, c2, c3 = st.columns(3)

with c1:
    st.markdown(
        f"""
        <div class="card">
          <div class="pill">📍 Localização</div>
          <div class="muted">Coordenadas</div>
          <div class="big">{lat:.6f}, {lon:.6f}</div>
          <div class="muted" style="margin-top:10px;">Zona</div>
          <div class="big">{res.get("zona_nome") or "—"}</div>
          <div class="muted" style="margin-top:10px;">Sigla</div>
          <div class="big">{res.get("zona_sigla") or "—"}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with c2:
    st.markdown(
        f"""
        <div class="card">
          <div class="pill">🛣️ Via</div>
          <div class="muted">Rua</div>
          <div class="big">{res.get("rua_nome") or "—"}</div>
          <div class="muted" style="margin-top:10px;">Hierarquia</div>
          <div class="big">{res.get("hierarquia") or "—"}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with c3:
    area_lote = calc.get("area_lote")
    st.markdown(
        f"""
        <div class="card">
          <div class="pill">🏡 Lote / Uso</div>
          <div class="muted">Uso</div>
          <div class="big">{calc.get("use_label")}</div>
          <div class="muted" style="margin-top:10px;">Área do lote</div>
          <div class="big">{fmt_m2(area_lote)}</div>
          <div class="muted" style="margin-top:10px;">Esquina</div>
          <div class="big">{"Sim" if calc.get("esquina") else "Não"}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

rule = calc.get("rule")
if not rule:
    st.warning(f"Sem regra cadastrada no Supabase para **{calc.get('zona_sigla')} + {calc.get('use_code')}**.")
    st.caption("Cadastre em `zone_rules` (TO/TP/IA/recuos/gabarito) e tente novamente.")
    st.stop()

# =============================
# Parâmetros detalhados (Supabase)
# =============================
st.divider()
st.markdown("## Parâmetros da Zona (detalhado)")

to_max = rule.get("to_max")
tp_min = rule.get("tp_min")
ia_max = rule.get("ia_max")
ia_min = rule.get("ia_min")
to_subsolo_max = rule.get("to_subsolo_max")  # NOVO

rec_fr = rule.get("recuo_frontal_m")
rec_lat = rule.get("recuo_lateral_m")
rec_fun = rule.get("recuo_fundos_m")

g_m = rule.get("gabarito_m")
g_pav = rule.get("gabarito_pav")

area_min_lote = rule.get("area_min_lote_m2")
area_max_lote = rule.get("area_max_lote_m2")  # NOVO

testada_min_meio = rule.get("testada_min_meio_m")
testada_min_esquina = rule.get("testada_min_esquina_m")
testada_max = rule.get("testada_max_m")  # NOVO

allow_attach = bool(rule.get("allow_attach_one_side") or False)

p1, p2, p3 = st.columns(3)

with p1:
    st.markdown(
        f"""
        <div class="card">
          <div class="pill">📌 Recuos</div>
          <div class="muted">Frontal</div><div class="big">{fmt_m(rec_fr)}</div>
          <div class="muted">Lateral</div><div class="big">{fmt_m(rec_lat)}</div>
          <div class="muted">Fundo</div><div class="big">{fmt_m(rec_fun)}</div>
          <div class="muted" style="margin-top:10px;">Encostar 1 lateral</div>
          <div class="big">{"Sim" if allow_attach else "Não"}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with p2:
    st.markdown(
        f"""
        <div class="card">
          <div class="pill">📊 Índices</div>
          <div class="muted">TO (máx)</div><div class="big">{fmt_pct(to_max)}</div>
          <div class="muted">TP/Permeabilidade (mín)</div><div class="big">{fmt_pct(tp_min)}</div>
          <div class="muted">IA (mín)</div><div class="big">{ia_min if ia_min is not None else "—"}</div>
          <div class="muted">IA (máx)</div><div class="big">{ia_max if ia_max is not None else "—"}</div>
          <div class="muted">TO Subsolo (máx)</div><div class="big">{fmt_pct(to_subsolo_max) if to_subsolo_max is not None else "—"}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with p3:
    gab_txt = "—"
    if g_pav not in (None, "", 0):
        gab_txt = f"{g_pav} pav"
    elif g_m is not None:
        gab_txt = fmt_m(g_m)

    st.markdown(
        f"""
        <div class="card">
          <div class="pill">📏 Lote / Altura</div>
          <div class="muted">Área mínima</div><div class="big">{fmt_m2(area_min_lote)}</div>
          <div class="muted">Área máxima</div><div class="big">{fmt_m2(area_max_lote) if area_max_lote is not None else "—"}</div>
          <div class="muted">Testada mín. (meio)</div><div class="big">{fmt_m(testada_min_meio)}</div>
          <div class="muted">Testada mín. (esquina)</div><div class="big">{fmt_m(testada_min_esquina)}</div>
          <div class="muted">Testada máx.</div><div class="big">{fmt_m(testada_max) if testada_max is not None else "—"}</div>
          <div class="muted" style="margin-top:8px;">Gabarito</div>
          <div class="big">{gab_txt}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

# alerta rápido se faltarem colunas novas
if to_subsolo_max is None or area_max_lote is None or testada_max is None:
    st.markdown(
        "<div class='warn'><b>⚠️ Alguns parâmetros ainda não estão preenchidos</b><br/>"
        "Se você ainda não criou as colunas novas no Supabase (TO Subsolo / Área Máx / Testada Máx), "
        "crie e preencha na tabela <b>zone_rules</b>.</div>",
        unsafe_allow_html=True,
    )

# =============================
# Resumo leigo-friendly
# =============================
st.divider()
st.markdown("## Resumo do que você pode fazer (modo simples)")

area_terreo = calc.get("area_max_ocupacao_real")
area_total = calc.get("area_max_total_construida")
area_perm = calc.get("area_min_permeavel")
area_subsolo = calc.get("area_max_subsolo")

pavs = calc.get("pavimentos_estimados")
g_pav = calc.get("gabarito_pav")
g_m = calc.get("gabarito_m")

extra_corner = ""
if calc.get("esquina"):
    modelo = calc.get("esquina_modelo") or "—"
    extra_corner = f"<div class='muted' style='margin-top:6px;'>Modelo de esquina usado: <b>{modelo}</b></div>"

extra_attach = ""
if calc.get("attach_one_side"):
    extra_attach = "<div class='ok'><b>✅ Encostando em 1 lateral</b><br/>Você marcou a opção de zerar 1 recuo lateral.</div>"

st.markdown(
    f"""
    <div class="card">
      <h4>✅ Ocupação no térreo</h4>
      <div class="big">Seu lote tem {fmt_m2(calc.get("area_lote"))}. No térreo, você pode ocupar até {fmt_m2(area_terreo)}.</div>
      <div class="muted">Esse limite considera TO e recuos (a regra mais restritiva vence).</div>
      {extra_corner}
    </div>
    {extra_attach}
    """,
    unsafe_allow_html=True,
)

st.markdown(
    f"""
    <div class="card" style="margin-top:12px;">
      <h4>🌿 Área permeável</h4>
      <div class="big">Você precisa deixar {fmt_m2(area_perm)} permeável.</div>
      <div class="muted">Ex.: jardins, solo natural, áreas drenantes (depende do que a prefeitura aceita).</div>
    </div>
    """,
    unsafe_allow_html=True,
)

if area_subsolo is not None:
    st.markdown(
        f"""
        <div class="card" style="margin-top:12px;">
          <h4>🅿️ Subsolo (limite por TO Subsolo)</h4>
          <div class="big">Área máxima de subsolo (por taxa): {fmt_m2(area_subsolo)}</div>
          <div class="muted">Depende das regras de subsolo da prefeitura (acessos, ventilação, etc.).</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

total_txt = fmt_m2(area_total)
pav_txt = f"{pavs} pavimentos (estimativa)" if pavs is not None else "—"
if g_pav not in (None, "", 0):
    altura_txt = f"Limite de altura: até {g_pav} pavimentos (pela regra)."
elif g_m is not None:
    altura_txt = f"Limite de altura: até {fmt_m(g_m)} (estimamos {pav_txt})."
else:
    altura_txt = "Limite de altura ainda não cadastrado para essa regra."

st.markdown(
    f"""
    <div class="card" style="margin-top:12px;">
      <h4>🏗️ Total construído (somando pavimentos)</h4>
      <div class="big">O total construído permitido é {total_txt} — isso inclui todos os pavimentos somados.</div>
      <div class="muted">{altura_txt}</div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.divider()
st.markdown("### Por que o térreo ficou nesse valor?")

colA, colB, colC = st.columns(3)
with colA:
    st.markdown(
        f"""
        <div class="card">
          <div class="muted">Limite por TO</div>
          <div class="big">{fmt_m2(calc.get("area_max_ocupacao_to"))}</div>
          <div class="muted">{fmt_pct(calc.get("to_max"))} do lote</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
with colB:
    st.markdown(
        f"""
        <div class="card">
          <div class="muted">Limite por recuos (miolo)</div>
          <div class="big">{fmt_m2(calc.get("area_miolo"))}</div>
          <div class="muted">({fmt_m(calc.get("largura_util_miolo"))} × {fmt_m(calc.get("prof_util_miolo"))})</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
with colC:
    st.markdown(
        f"""
        <div class="card">
          <div class="muted">O que vale no térreo</div>
          <div class="big">{fmt_m2(calc.get("area_max_ocupacao_real"))}</div>
          <div class="muted">Sempre o menor entre TO e miolo</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

if calc.get("vagas_min") is not None:
    st.divider()
    st.markdown("## Vagas mínimas")
    st.markdown(
        f"""
        <div class="card">
          <h4>🚗 Estacionamento</h4>
          <div class="big">Vagas mínimas: {int(calc.get("vagas_min"))}</div>
          <div class="muted">Regra puxada do Supabase (tabela parking_rules).</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

if calc.get("notes"):
    st.markdown("<div class='warn'><b>Observação</b><br/>" + str(calc.get("notes")) + "</div>", unsafe_allow_html=True)

if calc.get("observacoes"):
    st.divider()
    st.markdown("## Observações")
    st.write(calc.get("observacoes"))

if calc.get("source_ref"):
    st.caption(f"Fonte: {calc.get('source_ref')}")

with st.expander("Debug (raw)"):
    st.write("location:")
    st.json(res or {})
    st.write("rule:")
    st.json(calc.get("rule") or {})
    st.write("parking:")
    st.json(calc.get("park") or {})
