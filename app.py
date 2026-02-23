import os
import json
import math
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
      .sidebar-section {
        padding: 6px 0 14px 0;
        border-bottom: 1px solid rgba(49, 51, 63, 0.10);
        margin-bottom: 12px;
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
    z = json.loads(json.dumps(geojson))
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
          <div style="color:#666;">Preencha os dados e clique em <b>Gerar Estudo</b> para ver zona, rua e índices.</div>
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
        return {
            "largura_util": largura_util,
            "prof_util": prof_util,
            "area_miolo": largura_util * prof_util,
            "esquina_modelo": "meio_quadra",
        }

    if corner_two_fronts:
        lat_internal = rec_lat
        if attach_one_side:
            lat_internal = 0.0
        largura_util = max(testada - (lat_internal + rec_fr), 0.0)
        prof_util = max(profundidade - rec_fr - rec_fun, 0.0)
        return {
            "largura_util": largura_util,
            "prof_util": prof_util,
            "area_miolo": largura_util * prof_util,
            "esquina_modelo": "esquina_2_frentes",
        }

    lat_internal = rec_lat
    lat_other = rec_lat
    if attach_one_side:
        lat_internal = 0.0
    largura_util = max(testada - (lat_internal + lat_other), 0.0)
    prof_util = max(profundidade - rec_fr - rec_fun, 0.0)
    return {
        "largura_util": largura_util,
        "prof_util": prof_util,
        "area_miolo": largura_util * prof_util,
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

    if _tree_returns_indices(candidates):
        for i in candidates:
            try:
                i = int(i)
                if preps_list[i].contains(p) or geoms[i].intersects(p):
                    return props_list[i]
            except Exception:
                continue
        return None

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
            g = shape(geom)
            g_m = transform(_to_3857, g)
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

        if _is_index(nearest):
            i = int(nearest)
            d = p_m.distance(geoms_m[i])
            if d > max_dist_m:
                return None
            return props_list[i]

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
    res = sb.table("use_types").select("code,label,category").eq("is_active", True).order("category").order("label").execute()
    return res.data or []


@st.cache_data(show_spinner=False, ttl=300)
def sb_get_zone_rule(zone_sigla: str, use_type_code: str) -> Optional[Dict[str, Any]]:
    if not zone_sigla or not use_type_code:
        return None

    res = (
        sb.table("zone_rules")
        .select(
            "zone_sigla,use_type_code,"
            "to_max,tp_min,ia_min,ia_max,to_sub_max,"
            "recuo_frontal_m,recuo_lateral_m,recuo_fundos_m,"
            "gabarito_m,gabarito_pav,"
            "area_min_lote_m2,area_max_lote_m2,"
            "testada_min_meio_m,testada_min_esquina_m,testada_max_m,"
            "allow_attach_one_side,notes,special_area_tag,"
            "observacoes,source_ref,requires_subzone,subzone_code"
        )
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
        .select("use_type_code,metric,value,min_vagas,source_ref,rule_json")
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
    qtd_unidades: Optional[int] = None,
    area_unidade_m2: Optional[float] = None,
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
        "qtd_unidades": qtd_unidades,
        "area_unidade_m2": area_unidade_m2,
    }

    if rule:
        to_max = rule.get("to_max")
        tp_min = rule.get("tp_min")
        ia_max = rule.get("ia_max")
        ia_min = rule.get("ia_min")
        to_sub_max = rule.get("to_sub_max")

        rec_fr = rule.get("recuo_frontal_m")
        rec_lat = rule.get("recuo_lateral_m")
        rec_fun = rule.get("recuo_fundos_m")

        g_m = rule.get("gabarito_m")
        g_pav = rule.get("gabarito_pav")

        calc["to_max"] = to_max
        calc["tp_min"] = tp_min
        calc["ia_min"] = ia_min
        calc["ia_max"] = ia_max
        calc["to_sub_max"] = to_sub_max

        calc["area_max_ocupacao_to"] = (float(to_max) * area_lote) if to_max is not None else None
        calc["area_min_permeavel"] = (float(tp_min) * area_lote) if tp_min is not None else None
        calc["area_max_total_construida"] = (float(ia_max) * area_lote) if ia_max is not None else None
        calc["area_max_subsolo"] = (float(to_sub_max) * area_lote) if to_sub_max not in (None, "") else None

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
        calc["requires_subzone"] = bool(rule.get("requires_subzone") or False)
        calc["subzone_code"] = rule.get("subzone_code")

        calc["observacoes"] = rule.get("observacoes")
        calc["source_ref"] = rule.get("source_ref")

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

    # =============================
    # Vagas: suporta json_rule (RES_MULTI)
    # =============================
    vagas = None
    vagas_texto = None
    vagas_moto_txt = None

    if park:
        metric = park.get("metric")
        value = park.get("value") or 0
        min_v = park.get("min_vagas")
        rule_json = park.get("rule_json") or {}

        if metric == "fixed":
            try:
                vagas = int(value)
            except Exception:
                vagas = None

        elif metric == "per_unit":
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

        elif metric == "json_rule":
            rtype = (rule_json or {}).get("type")

            if rtype == "per_unit_by_unit_area":
                vagas_texto = (rule_json or {}).get("display_text")

                moto_pct = (rule_json or {}).get("moto_percent_max")
                if moto_pct is not None:
                    try:
                        vagas_moto_txt = f"Até {float(moto_pct) * 100:.0f}% das vagas podem ser destinadas a motos."
                    except Exception:
                        vagas_moto_txt = None

                thr = float((rule_json or {}).get("threshold_unit_area_m2", 90))
                rate_below = float((rule_json or {}).get("rate_below", 1.0))
                rate_at_or_above = float((rule_json or {}).get("rate_at_or_above", 1.5))
                rounding = (rule_json or {}).get("rounding", "ceil")

                if qtd_unidades and area_unidade_m2:
                    try:
                        qtd_unidades_i = int(qtd_unidades)
                        area_u = float(area_unidade_m2)

                        rate = rate_below if area_u < thr else rate_at_or_above
                        raw = qtd_unidades_i * rate

                        if rounding == "ceil":
                            vagas = int(math.ceil(raw))
                        else:
                            vagas = int(round(raw))

                        if min_v is not None:
                            vagas = max(vagas, int(min_v))
                    except Exception:
                        vagas = None

    calc["vagas_min"] = vagas
    calc["vagas_texto"] = vagas_texto
    calc["vagas_moto_txt"] = vagas_moto_txt
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
if "attach_one_side" not in st.session_state:
    st.session_state["attach_one_side"] = False


# =============================
# Sidebar (Categoria + Busca Direta)
# =============================
use_types = sb_list_use_types()

# fallback mínimo (se o banco estiver vazio)
if not use_types:
    use_types = [
        {"code": "RES_UNI", "label": "Residencial Unifamiliar (Casa)", "category": "Residencial"},
        {"code": "RES_MULTI", "label": "Residencial Multifamiliar (Prédio)", "category": "Residencial"},
    ]

# categorias ordenadas
preferred_order = ["Residencial", "Comercial", "Serviço", "Saúde/Educação", "Institucional", "Industrial", "Misto", "Sistema"]
cats = sorted({(u.get("category") or "Sistema") for u in use_types}, key=lambda c: (preferred_order.index(c) if c in preferred_order else 999, c))

st.sidebar.markdown("<div class='sidebar-section'><b>📋 1. Escolha o Uso</b></div>", unsafe_allow_html=True)

cat_selected = st.sidebar.selectbox("Categoria:", cats, index=(cats.index("Residencial") if "Residencial" in cats else 0))

in_cat = [u for u in use_types if (u.get("category") or "Sistema") == cat_selected]
cat_options = {u["label"]: u["code"] for u in in_cat}
if not cat_options:
    cat_options = {"Genérico (fallback) • Sistema": "SYS_FALLBACK"}

use_label_cat = st.sidebar.selectbox("Opções na Categoria:", list(cat_options.keys()))
use_code_cat = cat_options[use_label_cat]

st.sidebar.markdown("<div class='sidebar-section' style='margin-top:10px;'><b>🔎 2. Busca Direta</b></div>", unsafe_allow_html=True)

# itens da busca global (com prefixo da categoria)
search_items = []
search_map = {}
for u in use_types:
    c = (u.get("category") or "Sistema")
    label = u.get("label") or u.get("code")
    k = f"{c}: {label}"
    search_items.append(k)
    search_map[k] = (label, u.get("code"))

search_items = sorted(search_items, key=lambda x: x.lower())

search_pick = st.sidebar.selectbox("Ou digite para pesquisar:", ["—"] + search_items)
use_search = st.sidebar.checkbox("Usar seleção da Busca Direta", value=False)

# Escolha final do uso (categoria vs busca)
if use_search and search_pick != "—":
    use_label, use_code = search_map[search_pick]
    use_category = search_pick.split(":")[0]
else:
    use_label, use_code = use_label_cat, use_code_cat
    use_category = cat_selected


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
        tooltip=folium.GeoJsonTooltip(fields=list(zone_fields), aliases=zone_aliases, sticky=True, labels=True),
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
            st.rerun()


with col_panel:
    st.subheader("Selecione o lote no mapa")

    click = st.session_state["click"]
    if not click:
        st.info("Clique no mapa para marcar um ponto.")
        st.stop()

    lat = float(click["lat"])
    lon = float(click["lng"])

    st.markdown(f"**Uso selecionado:** {use_label}  \n**Categoria:** {use_category}")

    testada = st.number_input("Testada / Frente (m)", min_value=1.0, value=10.0, step=0.5)
    profundidade = st.number_input("Profundidade / Lateral (m)", min_value=1.0, value=30.0, step=0.5)

    esquina = st.checkbox("Lote de esquina", value=False)
    corner_two_fronts = True
    if esquina:
        corner_two_fronts = st.checkbox("Considerar 2 frentes (esquina)", value=True)

    # Multifamiliar (opcional)
    qtd_unidades = None
    area_unidade_m2 = None
    if use_code == "RES_MULTI":
        st.subheader("Dados do multifamiliar (opcional)")
        qtd_u = st.number_input("Quantidade de apartamentos (opcional)", min_value=0, value=0, step=1)
        area_u = st.number_input("Área média do apartamento (m²) (opcional)", min_value=0.0, value=0.0, step=5.0)
        qtd_unidades = int(qtd_u) if qtd_u and qtd_u > 0 else None
        area_unidade_m2 = float(area_u) if area_u and area_u > 0 else None

    # Encostar (controlado pela regra + travado para multifamiliar por segurança)
    last_calc = st.session_state.get("calc") or {}
    last_rule = (last_calc.get("rule") or {}) if isinstance(last_calc, dict) else {}
    allow_attach_last = bool(last_rule.get("allow_attach_one_side") or False)

    disabled_attach = (use_code == "RES_MULTI") or (not allow_attach_last)

    st.session_state["attach_one_side"] = st.checkbox(
        "Encostar em 1 lateral (zerar recuo)",
        value=bool(st.session_state.get("attach_one_side", False)),
        disabled=disabled_attach,
        help="Só habilita se a regra da zona permitir. Multifamiliar fica desabilitado por padrão (mais seguro)."
    )

    st.subheader("Calcular")

    if st.button("🚀 GERAR ESTUDO DE VIABILIDADE", use_container_width=True):
        with st.spinner("Calculando..."):
            res = compute_location(zone_index, ruas_index, lat, lon)
            st.session_state["res"] = res

            zona_sigla = res.get("zona_sigla") or ""
            rule = sb_get_zone_rule(zona_sigla, use_code)
            park = sb_get_parking_rule(use_code)

            allow_attach_now = bool((rule or {}).get("allow_attach_one_side") or False)
            attach_one_side = bool(st.session_state.get("attach_one_side", False)) and allow_attach_now and (use_code != "RES_MULTI")

            calc = compute_urbanism(
                zone_sigla=zona_sigla,
                use_label=use_label,
                use_code=use_code,
                testada=float(testada),
                profundidade=float(profundidade),
                esquina=bool(esquina),
                corner_two_fronts=bool(corner_two_fronts),
                attach_one_side=bool(attach_one_side),
                rule=rule,
                park=park,
                qtd_unidades=qtd_unidades,
                area_unidade_m2=area_unidade_m2,
            )
            st.session_state["calc"] = calc

        st.rerun()

    st.caption("💡 Dica: o pin aparece na hora. O cálculo acontece só quando você clicar em Gerar Estudo.")


# =============================
# RESULTADOS
# =============================
res = st.session_state.get("res")
calc = st.session_state.get("calc")

st.divider()
st.markdown("## Resultados")

if not res or not calc:
    st.caption("Clique no mapa, preencha os dados e depois clique em **Gerar Estudo**.")
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
    st.caption("Cadastre em `zone_rules` e tente novamente.")
    st.stop()

st.divider()
st.markdown("## Parâmetros da Zona (detalhado)")

to_max = rule.get("to_max")
tp_min = rule.get("tp_min")
ia_max = rule.get("ia_max")
ia_min = rule.get("ia_min")
to_sub_max = rule.get("to_sub_max")

rec_fr = rule.get("recuo_frontal_m")
rec_lat = rule.get("recuo_lateral_m")
rec_fun = rule.get("recuo_fundos_m")

g_m = rule.get("gabarito_m")
g_pav = rule.get("gabarito_pav")

area_min_lote = rule.get("area_min_lote_m2")
area_max_lote = rule.get("area_max_lote_m2")

testada_min_meio = rule.get("testada_min_meio_m")
testada_min_esquina = rule.get("testada_min_esquina_m")
testada_max = rule.get("testada_max_m")

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
          <div class="muted">TO Subsolo (máx)</div><div class="big">{fmt_pct(to_sub_max)}</div>
          <div class="muted">IA (mín)</div><div class="big">{ia_min if ia_min is not None else "—"}</div>
          <div class="muted">IA (máx)</div><div class="big">{ia_max if ia_max is not None else "—"}</div>
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
          <div class="muted">Área máxima</div><div class="big">{fmt_m2(area_max_lote)}</div>
          <div class="muted">Testada mín. (meio)</div><div class="big">{fmt_m(testada_min_meio)}</div>
          <div class="muted">Testada mín. (esquina)</div><div class="big">{fmt_m(testada_min_esquina)}</div>
          <div class="muted">Testada máx.</div><div class="big">{fmt_m(testada_max)}</div>
          <div class="muted" style="margin-top:8px;">Gabarito</div>
          <div class="big">{gab_txt}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

if rule.get("notes"):
    st.markdown("<div class='warn'><b>Observação</b><br/>" + str(rule.get("notes")) + "</div>", unsafe_allow_html=True)

if rule.get("observacoes"):
    st.markdown("<div class='warn'><b>Observações</b><br/>" + str(rule.get("observacoes")) + "</div>", unsafe_allow_html=True)

if rule.get("source_ref"):
    st.caption(f"Fonte: {rule.get('source_ref')}")

st.divider()
st.markdown("## Vagas mínimas")

if calc.get("vagas_min") is not None:
    extra = ""
    if calc.get("vagas_moto_txt"):
        extra = f"<div class='muted' style='margin-top:8px;'>{calc.get('vagas_moto_txt')}</div>"

    st.markdown(
        f"""
        <div class="card">
          <h4>🚗 Estacionamento</h4>
          <div class="big">Vagas mínimas estimadas: {int(calc.get("vagas_min"))}</div>
          <div class="muted">Cálculo feito com base nos dados informados.</div>
          {extra}
        </div>
        """,
        unsafe_allow_html=True,
    )
elif calc.get("vagas_texto"):
    extra = ""
    if calc.get("vagas_moto_txt"):
        extra = f"<div class='muted' style='margin-top:8px;'>{calc.get('vagas_moto_txt')}</div>"

    st.markdown(
        f"""
        <div class="card">
          <h4>🚗 Estacionamento</h4>
          <div class="big">Regra:</div>
          <div class="muted">{calc.get("vagas_texto")}</div>
          {extra}
          <div class="muted" style="margin-top:8px;">
            Para calcular automaticamente, informe <b>quantidade de apartamentos</b> e <b>área média</b> (apenas no multifamiliar).
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
else:
    st.caption("Sem regra de estacionamento cadastrada para este uso.")

with st.expander("Debug (raw)"):
    st.write("location:")
    st.json(res or {})
    st.write("rule:")
    st.json(calc.get("rule") or {})
    st.write("parking:")
    st.json(calc.get("park") or {})
