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
      .warn {
        border: 1px solid rgba(255, 107, 107, 0.35);
        background: rgba(255, 107, 107, 0.06);
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


def to_float_maybe(x):
    if x is None:
        return None
    try:
        if isinstance(x, str):
            # aceita "1,5" e "30.0"
            x = x.replace("m²", "").replace("m", "").strip()
            x = x.replace(".", "").replace(",", ".") if ("," in x and "." in x) else x.replace(",", ".")
        return float(x)
    except Exception:
        return None


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
    res = sb.table("use_types").select("code,label,category").eq("is_active", True).order("label").execute()
    return res.data or []


@st.cache_data(show_spinner=False, ttl=300)
def sb_get_zone_rule(zone_sigla: str, use_type_code: str) -> Optional[Dict[str, Any]]:
    if not zone_sigla or not use_type_code:
        return None
    res = (
        sb.table("zone_rules")
        .select(
            "zone_sigla,use_type_code,to_max,tp_min,ia_max,"
            "recuo_frontal_m,recuo_lateral_m,recuo_fundos_m,"
            "gabarito_m,gabarito_pav,observacoes,source_ref,"
            "area_min_lote_m2,testada_min_meio_m,testada_min_esquina_m,"
            "allow_attach_one_side,corner_two_fronts"
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
        .select("use_type_code,metric,value,min_vagas,source_ref")
        .eq("use_type_code", use_type_code)
        .limit(1)
        .execute()
    )
    data = res.data or []
    return data[0] if data else None


# =============================
# Cálculos urbanísticos (MVP+)
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

    if not esquina:
        lat_internal = float(rec_lat)
        lat_other = float(rec_lat)
        if attach_one_side:
            lat_internal = 0.0  # zera uma lateral

        largura_util = max(testada - (lat_internal + lat_other), 0.0)
        prof_util = max(profundidade - float(rec_fr) - float(rec_fun), 0.0)
        area = largura_util * prof_util
        return {
            "largura_util": largura_util,
            "prof_util": prof_util,
            "area_miolo": area,
            "esquina_modelo": "meio_quadra",
        }

    # esquina
    if corner_two_fronts:
        # width perde: lateral interna (rec_lat ou 0) + frente secundária (rec_fr)
        lat_internal = float(rec_lat)
        if attach_one_side:
            lat_internal = 0.0
        largura_util = max(testada - (lat_internal + float(rec_fr)), 0.0)

        # depth perde: frente principal (rec_fr) + fundo (rec_fun)
        prof_util = max(profundidade - float(rec_fr) - float(rec_fun), 0.0)
        area = largura_util * prof_util
        return {
            "largura_util": largura_util,
            "prof_util": prof_util,
            "area_miolo": area,
            "esquina_modelo": "esquina_2_frentes",
        }

    # esquina mas sem considerar 2 frentes (vira meio de quadra)
    lat_internal = float(rec_lat)
    lat_other = float(rec_lat)
    if attach_one_side:
        lat_internal = 0.0
    largura_util = max(testada - (lat_internal + lat_other), 0.0)
    prof_util = max(profundidade - float(rec_fr) - float(rec_fun), 0.0)
    area = largura_util * prof_util
    return {
        "largura_util": largura_util,
        "prof_util": prof_util,
        "area_miolo": area,
        "esquina_modelo": "esquina_sem_2_frentes",
    }


def compute_urbanism(
    zone_sigla: str,
    use_label: str,
    use_code: str,
    testada: float,
    profundidade: float,
    esquina: bool,
    attach_one_side_ui: bool,
    corner_two_fronts_ui: bool,
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
        "area_lote": area_lote,
        "rule": rule,
        "park": park,
        "attach_one_side_ui": bool(attach_one_side_ui),
        "corner_two_fronts_ui": bool(corner_two_fronts_ui),
        "validation_errors": [],
        "validation_warnings": [],
    }

    if rule:
        to_max = rule.get("to_max")
        tp_min = rule.get("tp_min")
        ia_max = rule.get("ia_max")

        rec_fr = rule.get("recuo_frontal_m")
        rec_lat = rule.get("recuo_lateral_m")
        rec_fun = rule.get("recuo_fundos_m")

        g_m = rule.get("gabarito_m")
        g_pav = rule.get("gabarito_pav")

        # validações mínimas
        area_min = rule.get("area_min_lote_m2")
        t_meio = rule.get("testada_min_meio_m")
        t_esq = rule.get("testada_min_esquina_m")

        if area_min is not None and area_lote < float(area_min):
            calc["validation_errors"].append(
                f"Área do lote ({area_lote:.2f} m²) menor que a mínima da zona ({float(area_min):.2f} m²)."
            )

        # testada
        if esquina:
            if t_esq is not None and float(testada) < float(t_esq):
                calc["validation_errors"].append(
                    f"Testada ({float(testada):.2f} m) menor que a mínima para esquina ({float(t_esq):.2f} m)."
                )
        else:
            if t_meio is not None and float(testada) < float(t_meio):
                calc["validation_errors"].append(
                    f"Testada ({float(testada):.2f} m) menor que a mínima para meio de quadra ({float(t_meio):.2f} m)."
                )

        calc["to_max"] = to_max
        calc["tp_min"] = tp_min
        calc["ia_max"] = ia_max

        calc["area_max_ocupacao_to"] = (float(to_max) * area_lote) if to_max is not None else None
        calc["area_min_permeavel"] = (float(tp_min) * area_lote) if tp_min is not None else None
        calc["area_max_total_construida"] = (float(ia_max) * area_lote) if ia_max is not None else None

        calc["recuo_frontal_m"] = rec_fr
        calc["recuo_lateral_m"] = rec_lat
        calc["recuo_fundos_m"] = rec_fun
        calc["gabarito_m"] = g_m
        calc["gabarito_pav"] = g_pav
        calc["observacoes"] = rule.get("observacoes")
        calc["source_ref"] = rule.get("source_ref")

        calc["area_min_lote_m2"] = area_min
        calc["testada_min_meio_m"] = t_meio
        calc["testada_min_esquina_m"] = t_esq
        calc["allow_attach_one_side"] = bool(rule.get("allow_attach_one_side") or False)
        calc["corner_two_fronts"] = bool(rule.get("corner_two_fronts") if rule.get("corner_two_fronts") is not None else True)

        # se UI pediu encostar mas regra não permite -> warning e desliga no cálculo
        attach_allowed = calc["allow_attach_one_side"]
        attach_effective = bool(attach_one_side_ui and attach_allowed and use_code == "RES_UNI")

        if attach_one_side_ui and not attach_allowed:
            calc["validation_warnings"].append("Encostar em 1 lateral não está liberado para esse uso/zona (regra Supabase).")

        # se esquina: usa corner_two_fronts do Supabase AND UI
        corner_two_fronts_effective = bool(calc["corner_two_fronts"] and corner_two_fronts_ui)

        # envelope padrão (sem encostar)
        if rec_lat is not None and rec_fr is not None and rec_fun is not None:
            env_padrao = envelope_area(
                testada=testada,
                profundidade=profundidade,
                rec_fr=float(rec_fr),
                rec_fun=float(rec_fun),
                rec_lat=float(rec_lat),
                esquina=bool(esquina),
                corner_two_fronts=corner_two_fronts_effective,
                attach_one_side=False,
            )

            env_encostar = envelope_area(
                testada=testada,
                profundidade=profundidade,
                rec_fr=float(rec_fr),
                rec_fun=float(rec_fun),
                rec_lat=float(rec_lat),
                esquina=bool(esquina),
                corner_two_fronts=corner_two_fronts_effective,
                attach_one_side=attach_effective,
            )

            calc["miolo_padrao"] = env_padrao
            calc["miolo_encostar"] = env_encostar

            # calcula área ocupação real nos 2 cenários
            area_to = calc.get("area_max_ocupacao_to")
            miolo_a = env_padrao["area_miolo"]
            miolo_b = env_encostar["area_miolo"]

            calc["area_max_ocupacao_real_padrao"] = min(float(area_to), float(miolo_a)) if area_to is not None else miolo_a
            calc["area_max_ocupacao_real_encostar"] = min(float(area_to), float(miolo_b)) if area_to is not None else miolo_b

            # escolhe uma “principal” pra manter o layout antigo (padrão)
            calc["area_miolo"] = miolo_a
            calc["largura_util_miolo"] = env_padrao["largura_util"]
            calc["prof_util_miolo"] = env_padrao["prof_util"]
            calc["area_max_ocupacao_real"] = calc["area_max_ocupacao_real_padrao"]
            calc["pavimentos_estimados"] = estimate_pavimentos(g_pav, g_m)
        else:
            calc["miolo_padrao"] = None
            calc["miolo_encostar"] = None

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
    esquina = st.checkbox("Lote de esquina")

    # extras para RES_UNI
    corner_two_fronts_ui = True
    attach_one_side_ui = False

    if esquina:
        corner_two_fronts_ui = st.checkbox("Considerar 2 frentes (esquina)", value=True)

    if use_code == "RES_UNI":
        attach_one_side_ui = st.checkbox("Encostar em 1 lateral (quando permitido)", value=False)

    st.subheader("3) Calcular")

    if st.button("🧮 Calcular", use_container_width=True):
        with st.spinner("Calculando..."):
            res = compute_location(zone_index, ruas_index, lat, lon)
            st.session_state["res"] = res

            zona_sigla = res.get("zona_sigla") or ""
            rule = sb_get_zone_rule(zona_sigla, use_code)
            park = sb_get_parking_rule(use_code)

            calc = compute_urbanism(
                zone_sigla=zona_sigla,
                use_label=use_label,
                use_code=use_code,
                testada=float(testada),
                profundidade=float(profundidade),
                esquina=bool(esquina),
                attach_one_side_ui=bool(attach_one_side_ui),
                corner_two_fronts_ui=bool(corner_two_fronts_ui),
                rule=rule,
                park=park,
            )
            st.session_state["calc"] = calc

        st.rerun()

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

if not (res.get("zona_sigla") or res.get("zona_nome")):
    st.warning("Não encontrei zona para esse ponto (verifique se clicou dentro do município/zoneamento).")
    with st.expander("Debug (raw)"):
        st.write("zone raw:")
        st.json(res.get("raw_zone") or {})
    st.stop()

rule = calc.get("rule")
if not rule:
    st.warning(f"Sem regra cadastrada no Supabase para **{calc.get('zona_sigla')} + {calc.get('use_code')}**.")
    st.caption("Cadastre em `zone_rules` (TO/TP/IA/recuos/gabarito) e tente novamente.")
    st.stop()

# validações
errs = calc.get("validation_errors") or []
warns = calc.get("validation_warnings") or []
if errs:
    st.markdown(
        f"""
        <div class="card warn">
          <h4>⚠️ Atenção: seu lote não atende os mínimos da zona</h4>
          <div class="muted">O sistema calcula, mas o projeto pode ser <b>reprovado</b> se isso não for ajustado.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    for e in errs:
        st.error(e)

if warns:
    for w in warns:
        st.warning(w)

# =============================
# INDICADORES DO ZONEAMENTO (MAPA)
# =============================
rawz = res.get("raw_zone") or {}
taxa_ocu_map = to_float_maybe(rawz.get("taxa_ocu"))
taxa_perm_map = to_float_maybe(rawz.get("taxa_perm"))
ia_map = to_float_maybe(rawz.get("indice_apr"))
rec_fr_map = get_prop(rawz, "rec_frente")
rec_fu_map = get_prop(rawz, "rec_fundo")
rec_lat_map = get_prop(rawz, "rec_latera")
area_min_map = get_prop(rawz, "area_min_l")
testada_min_map = get_prop(rawz, "testada_mi")
altura_map = get_prop(rawz, "altura_max")

st.divider()
st.markdown("## Indicadores do zoneamento (mapa)")

st.markdown(
    f"""
    <div class="card">
      <div class="muted">Esses valores vêm do seu arquivo de zoneamento (GeoJSON). Servem como referência visual.</div>
      <div style="margin-top:10px;">
        <b>TO (mapa):</b> {f"{taxa_ocu_map:.0f}%" if taxa_ocu_map is not None else "—"} &nbsp;&nbsp;|&nbsp;&nbsp;
        <b>TP (mapa):</b> {f"{taxa_perm_map:.0f}%" if taxa_perm_map is not None else "—"} &nbsp;&nbsp;|&nbsp;&nbsp;
        <b>IA (mapa):</b> {f"{ia_map:.2f}" if ia_map is not None else "—"}
      </div>
      <div style="margin-top:8px;">
        <b>Recuos (mapa):</b> Frente {rec_fr_map or "—"} | Fundo {rec_fu_map or "—"} | Laterais {rec_lat_map or "—"}
      </div>
      <div style="margin-top:8px;">
        <b>Área mín. (mapa):</b> {area_min_map or "—"} &nbsp;&nbsp;|&nbsp;&nbsp;
        <b>Testada mín. (mapa):</b> {testada_min_map or "—"} &nbsp;&nbsp;|&nbsp;&nbsp;
        <b>Altura máx (mapa):</b> {altura_map or "—"}
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# =============================
# RESUMO (SUPABASE = OFICIAL)
# =============================
st.divider()
st.markdown("## Resumo do que você pode fazer (modo simples)")

area_total = calc.get("area_max_total_construida")
area_perm = calc.get("area_min_permeavel")

pavs = calc.get("pavimentos_estimados")
g_pav = calc.get("gabarito_pav")
g_m = calc.get("gabarito_m")

# térreo: dois cenários (se houver miolos)
area_terreo_padrao = calc.get("area_max_ocupacao_real_padrao")
area_terreo_encostar = calc.get("area_max_ocupacao_real_encostar")
attach_allowed = bool(calc.get("allow_attach_one_side") and calc.get("use_code") == "RES_UNI")

st.markdown(
    f"""
    <div class="card">
      <h4>✅ Ocupação no térreo</h4>
      <div class="big">Seu lote tem {fmt_m2(calc.get("area_lote"))}.</div>
      <div style="margin-top:8px;">
        <b>Com recuos padrão:</b> até <b>{fmt_m2(area_terreo_padrao)}</b><br/>
        <b>Zerando 1 lateral (encostar):</b> até <b>{fmt_m2(area_terreo_encostar)}</b> {'<span class="muted">(quando permitido)</span>' if attach_allowed else '<span class="muted">(não liberado na regra)</span>'}
      </div>
      <div class="muted" style="margin-top:8px;">Esse limite considera TO e recuos (a regra mais restritiva vence).</div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    f"""
    <div class="card" style="margin-top:12px;">
      <h4>🌿 Área permeável</h4>
      <div class="big">Você precisa deixar {fmt_m2(area_perm)} permeável (área que absorve água).</div>
      <div class="muted">Ex.: jardins, solo natural, áreas drenantes (depende do que a prefeitura aceita).</div>
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

# =============================
# POR QUE DEU ESSE VALOR
# =============================
st.divider()
st.markdown("### Por que o térreo ficou nesse valor? (Supabase)")

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

miolo_padrao = (calc.get("miolo_padrao") or {})
miolo_enc = (calc.get("miolo_encostar") or {})

with colB:
    st.markdown(
        f"""
        <div class="card">
          <div class="muted">Miolo com recuos padrão</div>
          <div class="big">{fmt_m2(miolo_padrao.get("area_miolo"))}</div>
          <div class="muted">({fmt_m(miolo_padrao.get("largura_util"))} × {fmt_m(miolo_padrao.get("prof_util"))})</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with colC:
    st.markdown(
        f"""
        <div class="card">
          <div class="muted">Miolo zerando 1 lateral</div>
          <div class="big">{fmt_m2(miolo_enc.get("area_miolo"))}</div>
          <div class="muted">({fmt_m(miolo_enc.get("largura_util"))} × {fmt_m(miolo_enc.get("prof_util"))})</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.caption("➡️ O sistema sempre compara: **TO x miolo**. O que for menor é o que vale.")

# =============================
# PARÂMETROS USADOS NO CÁLCULO (SUPABASE)
# =============================
st.divider()
st.markdown("## Parâmetros usados no cálculo (Supabase)")

st.markdown(
    f"""
    <div class="card">
      <div class="muted">Esses são os valores oficiais do motor de cálculo (tabela <b>zone_rules</b>).</div>
      <div style="margin-top:10px;">
        <b>TO:</b> {fmt_pct(calc.get("to_max"))} &nbsp;&nbsp;|&nbsp;&nbsp;
        <b>TP:</b> {fmt_pct(calc.get("tp_min"))} &nbsp;&nbsp;|&nbsp;&nbsp;
        <b>IA:</b> {calc.get("ia_max") if calc.get("ia_max") is not None else "—"}
      </div>
      <div style="margin-top:8px;">
        <b>Recuos:</b> Frente {fmt_m(calc.get("recuo_frontal_m"))} | Fundo {fmt_m(calc.get("recuo_fundos_m"))} | Lateral {fmt_m(calc.get("recuo_lateral_m"))}
      </div>
      <div style="margin-top:8px;">
        <b>Área mín:</b> {fmt_m2(calc.get("area_min_lote_m2"))} &nbsp;&nbsp;|&nbsp;&nbsp;
        <b>Testada mín (meio):</b> {fmt_m(calc.get("testada_min_meio_m"))} &nbsp;&nbsp;|&nbsp;&nbsp;
        <b>Testada mín (esquina):</b> {fmt_m(calc.get("testada_min_esquina_m"))}
      </div>
      <div style="margin-top:8px;">
        <b>Encostar 1 lateral:</b> {"Sim" if calc.get("allow_attach_one_side") else "Não"} &nbsp;&nbsp;|&nbsp;&nbsp;
        <b>Esquina 2 frentes:</b> {"Sim" if calc.get("corner_two_fronts") else "Não"}
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# =============================
# Vagas
# =============================
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

# Observações / fonte
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
