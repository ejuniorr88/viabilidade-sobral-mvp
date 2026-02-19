import os
import json
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, Union

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

# parâmetro para estimar pavimentos quando só tiver gabarito em metros
ALTURA_PAV_ESTIMADA_M = 3.0


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
# Utils (format)
# =============================
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


def safe_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


# =============================
# GeoJSON helpers
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


def popup_html(clicked: bool):
    if not clicked:
        return """
        <div style="font-family: Arial, sans-serif; font-size: 13px; line-height: 1.35; min-width:260px;">
          <div style="font-weight:700; font-size:14px; margin-bottom:6px;">Selecione um ponto</div>
          <div style="color:#666;">Clique no mapa para marcar o lote.</div>
        </div>
        """
    return """
    <div style="font-family: Arial, sans-serif; font-size: 13px; line-height: 1.35; min-width:260px;">
      <div style="font-weight:700; font-size:14px; margin-bottom:6px;">Ponto selecionado</div>
      <div style="color:#666;">Preencha os dados ao lado e clique em <b>Calcular</b>.</div>
    </div>
    """


@st.cache_data(show_spinner=False)
def load_geojson(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _candidates_are_indices(cands) -> bool:
    """
    Shapely 2 pode retornar array de índices (np.int64).
    Shapely 1 geralmente retorna geometrias.
    """
    if cands is None:
        return False
    try:
        if len(cands) == 0:
            return True
        # np.int64 não é int puro; então tenta converter
        _ = int(cands[0])
        # mas se for geometria, int() falha
        return True
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
    return {"geoms": geoms, "preps": preps_list, "props": props_list, "tree": tree}


def find_zone_for_click(zone_index, lat: float, lon: float) -> Optional[dict]:
    tree = zone_index["tree"]
    if not tree:
        return None

    p = Point(lon, lat)
    cands = tree.query(p)

    geoms = zone_index["geoms"]
    preps_list = zone_index["preps"]
    props_list = zone_index["props"]

    # Caso A: índices
    if _candidates_are_indices(cands):
        for idx in cands:
            try:
                i = int(idx)
                if preps_list[i].contains(p) or geoms[i].intersects(p):
                    return props_list[i]
            except Exception:
                continue
        return None

    # Caso B: geometrias
    # (fallback mais lento, mas seguro)
    for g in cands:
        try:
            # achar índice por identidade é mais robusto que equality
            # mas aqui usamos .index como fallback
            i = geoms.index(g)
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
            g = shape(geom)                  # WGS84
            g_m = transform(_to_3857, g)      # metros
            geoms_m.append(g_m)
            props_list.append(props)
        except Exception:
            continue

    tree = STRtree(geoms_m) if geoms_m else None
    return {"geoms_m": geoms_m, "props": props_list, "tree": tree}


def find_nearest_street(ruas_index, lat: float, lon: float, max_dist_m: float = 120.0) -> Optional[dict]:
    if not ruas_index or not ruas_index["tree"]:
        return None

    p_m = transform(_to_3857, Point(lon, lat))
    tree = ruas_index["tree"]
    geoms_m = ruas_index["geoms_m"]
    props_list = ruas_index["props"]

    try:
        nearest = tree.nearest(p_m)

        # Shapely 2 pode retornar índice
        if isinstance(nearest, (int,)) or (nearest is not None and str(type(nearest)).find("numpy") >= 0):
            i = int(nearest)
            d = p_m.distance(geoms_m[i])
            if d > max_dist_m:
                return None
            return props_list[i]

        # Shapely 1 / alguns casos: retorna geometria
        if nearest is None:
            return None
        d = p_m.distance(nearest)
        if d > max_dist_m:
            return None

        # fallback para achar índice
        try:
            i = geoms_m.index(nearest)
            return props_list[i]
        except Exception:
            return None

    except Exception:
        return None


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
            "gabarito_m,gabarito_pav,observacoes,source_ref"
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
# Cálculos
# =============================
def estimate_pavimentos(gabarito_pav: Optional[int], gabarito_m: Optional[float]) -> Optional[int]:
    if gabarito_pav is not None:
        try:
            return int(gabarito_pav)
        except Exception:
            pass
    if gabarito_m is not None:
        try:
            return max(1, int(float(gabarito_m) // ALTURA_PAV_ESTIMADA_M))
        except Exception:
            return None
    return None


def calc_miolo_area(testada: float, profundidade: float, recuo_lat: Optional[float], recuo_front: Optional[float], recuo_fundos: Optional[float]) -> Tuple[Optional[float], Optional[str]]:
    if recuo_lat is None or recuo_front is None or recuo_fundos is None:
        return None, "Sem recuos completos para calcular o miolo."
    w = float(testada) - 2.0 * float(recuo_lat)
    d = float(profundidade) - float(recuo_front) - float(recuo_fundos)
    if w <= 0 or d <= 0:
        return 0.0, "Recuos inviabilizam área edificável (miolo ficou <= 0)."
    return w * d, None


def compute_all(
    zone_index,
    ruas_index,
    lat: float,
    lon: float,
    use_code: str,
    use_label: str,
    testada: float,
    profundidade: float,
    esquina: bool,
) -> Dict[str, Any]:
    props_zone = find_zone_for_click(zone_index, lat, lon)
    props_rua = find_nearest_street(ruas_index, lat, lon) if ruas_index else None

    zona_sigla = get_prop(props_zone or {}, "sigla", "SIGLA", "zona_sigla", "ZONA_SIGLA", "name")
    zona_nome = get_prop(props_zone or {}, "zona", "ZONA", "nome", "NOME")

    rua_nome = get_prop(props_rua or {}, "log_ofic", "LOG_OFIC", "name", "NOME")
    hierarquia = get_prop(props_rua or {}, "hierarquia", "HIERARQUIA")

    rule = sb_get_zone_rule(zona_sigla, use_code) if zona_sigla else None
    park = sb_get_parking_rule(use_code)

    area_lote = float(testada) * float(profundidade)

    out: Dict[str, Any] = {
        "lat": lat,
        "lon": lon,
        "zona_sigla": zona_sigla,
        "zona_nome": zona_nome,
        "rua_nome": rua_nome,
        "hierarquia": hierarquia,
        "use_code": use_code,
        "use_label": use_label,
        "testada": float(testada),
        "profundidade": float(profundidade),
        "esquina": bool(esquina),
        "area_lote": area_lote,
        "rule": rule,
        "park": park,
        "raw_zone": props_zone or {},
        "raw_rua": props_rua or {},
    }

    if not rule:
        return out

    # regras
    to_max = safe_float(rule.get("to_max"))
    tp_min = safe_float(rule.get("tp_min"))
    ia_max = safe_float(rule.get("ia_max"))

    recuo_front = safe_float(rule.get("recuo_frontal_m"))
    recuo_lat = safe_float(rule.get("recuo_lateral_m"))
    recuo_fundos = safe_float(rule.get("recuo_fundos_m"))

    gabarito_m = safe_float(rule.get("gabarito_m"))
    gabarito_pav = rule.get("gabarito_pav")

    out.update(
        {
            "to_max": to_max,
            "tp_min": tp_min,
            "ia_max": ia_max,
            "recuo_frontal_m": recuo_front,
            "recuo_lateral_m": recuo_lat,
            "recuo_fundos_m": recuo_fundos,
            "gabarito_m": gabarito_m,
            "gabarito_pav": gabarito_pav,
            "observacoes": rule.get("observacoes"),
            "source_ref": rule.get("source_ref"),
        }
    )

    # m² (TO/TP/IA)
    out["area_max_ocupacao_to"] = (to_max * area_lote) if to_max is not None else None
    out["area_min_permeavel"] = (tp_min * area_lote) if tp_min is not None else None
    out["area_max_total_construida"] = (ia_max * area_lote) if ia_max is not None else None

    # miolo (recuos)
    miolo, miolo_warn = calc_miolo_area(testada, profundidade, recuo_lat, recuo_front, recuo_fundos)
    out["area_miolo"] = miolo
    out["miolo_warn"] = miolo_warn

    # ocupação máxima real (menor entre TO e miolo, quando miolo existe)
    if out["area_max_ocupacao_to"] is not None and miolo is not None:
        out["area_max_ocupacao_real"] = min(out["area_max_ocupacao_to"], miolo)
    else:
        out["area_max_ocupacao_real"] = out["area_max_ocupacao_to"]

    # estimativa de pavimentos
    out["pavimentos_estimados"] = estimate_pavimentos(
        gabarito_pav if gabarito_pav not in (None, "") else None,
        gabarito_m,
    )

    # vagas (MVP)
    vagas = None
    if park:
        metric = park.get("metric")
        value = park.get("value") or 0
        min_v = park.get("min_vagas")
        if metric == "fixed":
            vagas = int(value)
        elif metric == "per_unit":
            if use_code == "RES_UNI":
                vagas = max(int(value), int(min_v or 0))
            else:
                vagas = None
        elif metric == "per_area":
            vagas = int((area_lote * float(value)) // 1)
            if min_v is not None:
                vagas = max(vagas, int(min_v))
    out["vagas_min"] = vagas

    return out


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
if "calc" not in st.session_state:
    st.session_state["calc"] = None


# =============================
# Top layout (Mapa + Inputs)
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
        lat0 = float(click["lat"])
        lon0 = float(click["lng"])
        html = popup_html(clicked=True)
        folium.Marker(
            location=[lat0, lon0],
            tooltip="Ponto selecionado",
            popup=folium.Popup(html, max_width=420, show=True),
            icon=folium.Icon(color="blue", icon="info-sign"),
        ).add_to(m)
        m.location = [lat0, lon0]
        m.zoom_start = 16
    else:
        # sem clique: popup "dummy" não precisa
        pass

    out = st_folium(m, width=1200, height=700, key="main_map")

    last = (out or {}).get("last_clicked")
    if last:
        new_click = {"lat": float(last["lat"]), "lng": float(last["lng"])}
        if st.session_state["click"] != new_click:
            st.session_state["click"] = new_click
            st.session_state["calc"] = None
            st.rerun()

with col_panel:
    st.subheader("Dados do lote/projeto")

    # uso (do Supabase)
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

    st.caption("Clique no mapa para marcar o lote. Depois, clique em **Calcular**.")

    can_calc = st.session_state["click"] is not None
    if st.button("🧮 Calcular", use_container_width=True, disabled=not can_calc):
        c = st.session_state["click"]
        lat = float(c["lat"])
        lon = float(c["lng"])

        with st.spinner("Calculando viabilidade (zona/rua + regras + índices)..."):
            st.session_state["calc"] = compute_all(
                zone_index=zone_index,
                ruas_index=ruas_index,
                lat=lat,
                lon=lon,
                use_code=use_code,
                use_label=use_label,
                testada=float(testada),
                profundidade=float(profundidade),
                esquina=bool(esquina),
            )
        st.rerun()

    if not can_calc:
        st.info("Primeiro clique no mapa para marcar o ponto do lote.")


# =============================
# Resultados (embaixo do mapa)
# =============================
st.divider()
st.subheader("Resultados")

calc = st.session_state.get("calc")
if not calc:
    st.caption("Após clicar no mapa e preencher os dados, clique em **Calcular** para ver o resultado aqui embaixo.")
    st.stop()

# --- resumo de localização
c1, c2, c3 = st.columns([1.3, 1.3, 1.4])
with c1:
    st.markdown("### Localização")
    st.write("**Coordenadas:**", f'{calc["lat"]:.6f}, {calc["lon"]:.6f}')
    st.write("**Zona:**", (calc.get("zona_nome") or "—"))
    st.write("**Sigla:**", (calc.get("zona_sigla") or "—"))
with c2:
    st.markdown("### Via")
    st.write("**Rua:**", (calc.get("rua_nome") or "—"))
    st.write("**Hierarquia:**", (calc.get("hierarquia") or "—"))
with c3:
    st.markdown("### Lote / Uso")
    st.write("**Uso:**", calc.get("use_label") or "—")
    st.write("**Área do lote:**", fmt_m2(calc.get("area_lote")))
    st.write("**Esquina:**", "Sim" if calc.get("esquina") else "Não")

# --- regra
rule = calc.get("rule")
if not calc.get("zona_sigla"):
    st.warning("Não consegui identificar a sigla da zona nesse ponto. Verifique se o ponto está dentro do zoneamento.")
    st.stop()

if not rule:
    st.warning(f"Sem regra cadastrada no Supabase para **{calc.get('zona_sigla')} + {calc.get('use_code')}**.")
    st.caption("Cadastre em `zone_rules` (TO/TP/IA/recuos/gabarito) e tente novamente.")
    st.stop()

st.divider()

# --- índices + m²
st.markdown("## Índices e áreas (em m²)")

idx1, idx2, idx3 = st.columns(3)
with idx1:
    st.metric("TO máx", fmt_pct(calc.get("to_max")))
    st.write("**TO × Área do lote:**", fmt_m2(calc.get("area_max_ocupacao_to")))
with idx2:
    st.metric("TP mín", fmt_pct(calc.get("tp_min")))
    st.write("**Área mín. permeável:**", fmt_m2(calc.get("area_min_permeavel")))
with idx3:
    ia = calc.get("ia_max")
    st.metric("IA máx", (f"{ia:.2f}" if isinstance(ia, (int, float)) else ("—" if ia is None else str(ia))))
    st.write("**Área máx. construída total (IA × lote):**", fmt_m2(calc.get("area_max_total_construida")))

# --- recuos + miolo
st.divider()
st.markdown("## Recuos e área edificável (miolo)")

r1, r2, r3, r4 = st.columns(4)
with r1:
    st.write("**Recuo frontal:**", fmt_m(calc.get("recuo_frontal_m")))
with r2:
    st.write("**Recuo lateral:**", fmt_m(calc.get("recuo_lateral_m")))
with r3:
    st.write("**Recuo fundos:**", fmt_m(calc.get("recuo_fundos_m")))
with r4:
    pav = calc.get("pavimentos_estimados")
    st.write("**Pavimentos (estim.):**", pav if pav is not None else "—")

miolo = calc.get("area_miolo")
miolo_warn = calc.get("miolo_warn")
if miolo is None:
    st.info("**Miolo não calculado**: faltam recuos completos na regra (frontal/lateral/fundos).")
else:
    st.write("**Área do miolo (por recuos):**", fmt_m2(miolo))
    if miolo_warn:
        st.warning(miolo_warn)

# --- comparação TO x miolo
st.divider()
st.markdown("## Ocupação máxima no térreo (comparação)")

area_to = calc.get("area_max_ocupacao_to")
area_real = calc.get("area_max_ocupacao_real")

cA, cB, cC = st.columns([1, 1, 1])
with cA:
    st.write("**Limite por TO:**", fmt_m2(area_to))
with cB:
    st.write("**Limite por recuos (miolo):**", fmt_m2(miolo) if miolo is not None else "—")
with cC:
    st.success(f"**Ocupação máx. recomendada (menor limite): {fmt_m2(area_real)}**")

# --- gabarito
st.divider()
st.markdown("## Gabarito")
st.write("**Gabarito (m):**", fmt_m(calc.get("gabarito_m")))
st.write("**Gabarito (pav):**", calc.get("gabarito_pav") or "—")
st.caption(f"Estimativa de pavimentos por {ALTURA_PAV_ESTIMADA_M:.1f} m/pav quando não existir gabarito em pav.")

# --- vagas
vagas = calc.get("vagas_min")
if vagas is not None:
    st.divider()
    st.markdown("## Vagas mínimas")
    st.write("**Vagas mín.:**", int(vagas))

# --- observações/fonte
if calc.get("observacoes"):
    st.divider()
    st.markdown("## Observações")
    st.write(calc.get("observacoes"))

if calc.get("source_ref"):
    st.caption(f"Fonte: {calc.get('source_ref')}")

with st.expander("Debug (raw)"):
    st.write("rule:")
    st.json(calc.get("rule") or {})
    st.write("parking:")
    st.json(calc.get("park") or {})
    st.write("raw zone props:")
    st.json(calc.get("raw_zone") or {})
    st.write("raw rua props:")
    st.json(calc.get("raw_rua") or {})
