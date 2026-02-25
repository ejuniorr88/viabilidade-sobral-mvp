def main():
    import os
    import json
    import math
    import re
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

    from core.report_unifamiliar import build_unifamiliar_report_md
    
    
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
    
    
    # -----------------------------
    # Robust residential detectors
    # (fix: garantir que "para leigo" apareça mesmo se codes/labels variam)
    # -----------------------------
    def _norm(s: str) -> str:
        return (s or "").strip()
    
    def _norm_l(s: str) -> str:
        return _norm(s).lower()
    
    def is_res_uni(use_code: str, use_label: str, use_category: str = "") -> bool:
        c = _norm(use_code).upper()
        l = _norm_l(use_label)
        cat = _norm_l(use_category)
        # code patterns
        if c.startswith("RES_UNI") or c in ("RESUNI", "RES_UNIF", "RES_UNIFAMILIAR"):
            return True
        if c.startswith("RES") and ("UNI" in c or "UNIF" in c):
            return True
        # label/category patterns
        if "unifamiliar" in l or ("casa" in l and "res" in l):
            return True
        if cat == "residencial" and ("unifamiliar" in l or "casa" in l):
            return True
        return False
    
    
    def is_res_multi(use_code: str, use_label: str, use_category: str = "") -> bool:
        c = _norm(use_code).upper()
        l = _norm_l(use_label)
        cat = _norm_l(use_category)
        if c.startswith("RES_MULTI") or c in ("RESMULTI", "RES_MF", "RES_MULTIFAMILIAR"):
            return True
        if c.startswith("RES") and ("MULTI" in c or "MF" in c):
            return True
        if "multifamiliar" in l or "prédio" in l or "predio" in l or ("apartamento" in l and "res" in l):
            return True
        if cat == "residencial" and ("multifamiliar" in l or "prédio" in l or "predio" in l or "apartamento" in l):
            return True
        return False
    
    
    def is_res_any(use_code: str, use_label: str, use_category: str = "") -> bool:
        return is_res_uni(use_code, use_label, use_category) or is_res_multi(use_code, use_label, use_category)
    
    
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
    # Sanitários (Anexo III) + Estacionamento v2 (Anexo IV)
    # =============================
    def _parse_formula_divisor(formula: str) -> Optional[float]:
        # "1/300,00m² ou fração" -> 300.0
        if not formula:
            return None
        m = re.search(r"1\s*/\s*([\d\.,]+)\s*m", formula)
        if not m:
            return None
        raw = m.group(1).replace(".", "").replace(",", ".")
        try:
            return float(raw)
        except Exception:
            return None
    
    
    def _ceil_div(a: float, b: float) -> int:
        return int(math.ceil(a / b)) if b and a is not None else 0
    
    
    def calc_sanitary(profile_json: dict, area_util_m2: float) -> Dict[str, Any]:
        area = float(area_util_m2 or 0)
        out = {"area_util_m2": area, "groups": {}, "totals": {}}
        totals: Dict[str, int] = {}
    
        keys = ["lavatórios", "aparelhos_sanitários", "chuveiros", "mictórios"]
    
        for grp in (profile_json.get("groups") or []):
            gname = grp.get("group") or "GERAL"
            chosen = None
    
            for b in (grp.get("bands") or []):
                mn = float(b.get("min_m2", 0))
                mx = b.get("max_m2", None)
                if area >= mn and (mx is None or area <= float(mx)):
                    chosen = b
                    break
    
            if not chosen:
                continue
    
            gvals: Dict[str, Any] = {}
            for k in keys:
                v = chosen.get(k, None)
                f = chosen.get(f"{k}_formula", None)
    
                if v is not None:
                    gvals[k] = int(v) if isinstance(v, (int, float)) else None
                elif f:
                    div = _parse_formula_divisor(f)
                    gvals[k] = _ceil_div(area, div) if div else None
                else:
                    gvals[k] = None
    
            if chosen.get("note"):
                gvals["_note"] = chosen["note"]
    
            out["groups"][gname] = gvals
    
            for k in keys:
                val = gvals.get(k)
                if isinstance(val, int):
                    totals[k] = totals.get(k, 0) + val
    
        out["totals"] = totals
        return out
    
    
    def round_rule_annex_iv(x: float) -> int:
        """
        Regra do Anexo IV:
        se o resultado for uma fração cujo décimo >= 5, arredonda pro inteiro superior.
        """
        if x <= 0:
            return 0
        v10 = round(x, 1)
        i = int(math.floor(v10))
        frac = v10 - i
        if frac >= 0.5:
            return i + 1
        return max(i, 0)
    
    
    def safe_eval_condition(cond: str, ctx: Dict[str, Any]) -> bool:
        if not cond:
            return True
        allowed = {"__builtins__": {}}
        return bool(eval(cond, allowed, ctx))
    
    
    def calc_parking_v2(rule_json: dict, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        rule_json deve vir do Supabase (parking_rules_v2.rule_json),
        seguindo o padrão do Anexo IV que você montou.
        """
        base_metric = rule_json.get("base_metric")
        rules = rule_json.get("rules") or []
    
        out = {
            "use_code": rule_json.get("use_code"),
            "base_metric": base_metric,
            "inputs": inputs,
            "raw": None,
            "required": None,
            "applied_rule_text": None,
            "adjustments": [],
            "cargo_loading_text": (rule_json.get("cargo_loading") or {}).get("text"),
            "notes": [],
            "general_notes": rule_json.get("general_notes") or [],
        }
    
        area = float(inputs.get("area_util_m2") or 0)
    
        # Dispensa: não residencial até 100m² em via local (quando base = área útil)
        if inputs.get("is_via_local") and base_metric == "area_util_m2":
            usec = (rule_json.get("use_code") or "").upper()
            is_res = usec.startswith("RES_UNI") or usec.startswith("RES_MULTI") or ("RESIDEN" in usec)
            if (0 < area <= 100) and (not is_res):
                out["raw"] = 0.0
                out["required"] = 0
                out["applied_rule_text"] = "Dispensa: não residencial ≤ 100m² em via local."
                return out
    
        raw = None
        applied_text = None
    
        for r in rules:
            rtype = r.get("type")
    
            if rtype == "fixed":
                try:
                    raw = float(r.get("value", 0))
                    applied_text = r.get("text")
                    break
                except Exception:
                    continue
    
            if rtype == "ratio":
                if base_metric == "area_util_m2":
                    per = float(r.get("per_m2"))
                    raw = area / per if per else 0.0
                    applied_text = r.get("text")
                    break
                else:
                    per_units = float(r.get("per_units"))
                    qty = float(inputs.get(base_metric) or 0)
                    raw = qty / per_units if per_units else 0.0
                    applied_text = r.get("text")
                    break
    
            elif rtype == "band_ratio":
                if base_metric != "area_util_m2":
                    continue
                for b in (r.get("bands") or []):
                    mn = float(b.get("min_m2", 0))
                    mx = b.get("max_m2", None)
                    if area >= mn and (mx is None or area <= float(mx)):
                        per = float(b.get("per_m2"))
                        raw = area / per if per else 0.0
                        applied_text = b.get("text")
                        break
                if raw is not None:
                    break
    
            elif rtype in ("threshold_fixed", "fixed_or_band"):
                if base_metric != "area_util_m2":
                    continue
                mx = r.get("max_m2", None)
                if mx is None:
                    out["notes"].append("Regra textual (fixed_or_band) - precisa padronizar em JSON calculável.")
                    continue
                mx = float(mx)
                if area <= mx:
                    raw = float(r.get("count", 0))
                    applied_text = r.get("text")
                    break
    
            elif rtype == "ratio_above_threshold":
                if base_metric != "area_util_m2":
                    continue
                mn = float(r.get("min_m2"))
                if area >= mn:
                    per = float(r.get("per_m2"))
                    raw = area / per if per else 0.0
                    applied_text = r.get("text")
                    break
    
            elif rtype in ("per_unit", "per_unit_with_condition"):
                qty = float(inputs.get("apartamentos") or 0)
                val = float(r.get("value", r.get("per_unit", 0)))
                cond = r.get("condition")
                ctx = dict(inputs)
                if cond:
                    if safe_eval_condition(cond, ctx):
                        raw = qty * val
                        applied_text = r.get("text")
                        break
                else:
                    raw = qty * val
                    applied_text = r.get("text")
                    break
    
        out["raw"] = raw
        out["applied_rule_text"] = applied_text
    
        if raw is None:
            out["required"] = None
            out["notes"].append("Sem dados suficientes para calcular automaticamente.")
            return out
    
        req = round_rule_annex_iv(float(raw))
    
        # redução VLT 20% (se marcado)
        if inputs.get("near_vlt") and req and req > 0:
            reduced = int(math.ceil(req * 0.8))
            out["adjustments"].append({"type": "VLT_20pct", "from": req, "to": reduced})
            req = reduced
    
        out["required"] = req
        return out
    
    
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
    
    
    # --- antigo (fallback) ---
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
    
    
    # --- novo (Anexo IV) ---
    @st.cache_data(show_spinner=False, ttl=300)
    def sb_get_parking_rule_v2(use_code: str) -> Optional[Dict[str, Any]]:
        if not use_code:
            return None
        res = (
            sb.table("parking_rules_v2")
            .select("use_code,base_metric,rule_json,general_notes,source_ref,notes")
            .eq("use_code", use_code)
            .limit(1)
            .execute()
        )
        data = res.data or []
        return data[0] if data else None
    
    
    # --- Sanitários (Anexo III) ---
    @st.cache_data(show_spinner=False, ttl=300)
    def sb_get_use_sanitary_profile(use_code: str) -> Optional[Dict[str, Any]]:
        if not use_code:
            return None
        res = (
            sb.table("use_sanitary_profile")
            .select("use_type_code,sanitary_profile,notes")
            .eq("use_type_code", use_code)
            .limit(1)
            .execute()
        )
        data = res.data or []
        return data[0] if data else None
    
    
    @st.cache_data(show_spinner=False, ttl=300)
    def sb_get_sanitary_profile(profile_code: str) -> Optional[Dict[str, Any]]:
        if not profile_code:
            return None
        res = (
            sb.table("sanitary_profiles")
            .select("sanitary_profile,title,rule_json,source_ref,notes")
            .eq("sanitary_profile", profile_code)
            .limit(1)
            .execute()
        )
        data = res.data or []
        return data[0] if data else None
    
    
    # =============================
    # Cálculos urbanísticos (seu motor atual)
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
            # =============================
            # Opções de implantação (Relatório - Unifamiliar)
            # Opção 1: recuos padrão da zona (com a geometria do lote)
            # Opção 2: flexibilidade Art. 112 (LC 90/2023) — pode zerar recuos de frente e laterais
            # (mantém recuo de fundo e mantém TO/TP da zona)
            # =============================
            if use_code == "res_unifamiliar":
                try:
                    # Opção 1 (padrão)
                    env_padrao = envelope_area(
                        testada=testada,
                        profundidade=profundidade,
                        rec_fr=float(rec_fr),
                        rec_fun=float(rec_fun),
                        rec_lat=float(rec_lat),
                        esquina=bool(esquina),
                        corner_two_fronts=bool(corner_two_fronts),
                        attach_one_side=bool(attach_one_side),
                    )
                    area_max_padrao = None
                    if area_to is not None and env_padrao.get("area_miolo") is not None:
                        area_max_padrao = min(float(area_to), float(env_padrao["area_miolo"]))
                    elif env_padrao.get("area_miolo") is not None:
                        area_max_padrao = float(env_padrao["area_miolo"])

                    # Opção 2 (Art. 112) — zera frente e laterais
                    env_art112 = envelope_area(
                        testada=testada,
                        profundidade=profundidade,
                        rec_fr=0.0,
                        rec_fun=float(rec_fun),
                        rec_lat=0.0,
                        esquina=bool(esquina),
                        corner_two_fronts=bool(corner_two_fronts),
                        attach_one_side=False,  # já está "colado" em ambas as laterais
                    )
                    area_max_art112 = None
                    if area_to is not None and env_art112.get("area_miolo") is not None:
                        area_max_art112 = min(float(area_to), float(env_art112["area_miolo"]))
                    elif env_art112.get("area_miolo") is not None:
                        area_max_art112 = float(env_art112["area_miolo"])

                    calc["opcao_1_recuos_padrao"] = {
                        "nome": "Recuos padrão da zona",
                        "base_legal": "Recuos da zona (padrão)",
                        "recuo_frontal_m": float(rec_fr),
                        "recuo_lateral_m": float(rec_lat),
                        "recuo_fundo_m": float(rec_fun),
                        "largura_util_m": float(env_padrao.get("largura_util") or 0.0),
                        "profundidade_util_m": float(env_padrao.get("prof_util") or 0.0),
                        "area_envelope_m2": float(env_padrao.get("area_miolo") or 0.0),
                        "area_max_terreo_m2": float(area_max_padrao or 0.0),
                        "limitante": "TO" if (area_to is not None and area_max_padrao is not None and float(area_to) <= float(env_padrao.get("area_miolo") or 0.0)) else "Recuos",
                    }
                    calc["opcao_2_art112_sem_recuos_frente_laterais"] = {
                        "nome": "Art. 112 (flexibilidade) — sem recuos de frente e laterais",
                        "base_legal": "LC 90/2023 — Art. 112 (Unifamiliar)",
                        "recuo_frontal_m": 0.0,
                        "recuo_lateral_m": 0.0,
                        "recuo_fundo_m": float(rec_fun),
                        "largura_util_m": float(env_art112.get("largura_util") or 0.0),
                        "profundidade_util_m": float(env_art112.get("prof_util") or 0.0),
                        "area_envelope_m2": float(env_art112.get("area_miolo") or 0.0),
                        "area_max_terreo_m2": float(area_max_art112 or 0.0),
                        "limitante": "TO" if (area_to is not None and area_max_art112 is not None and float(area_to) <= float(env_art112.get("area_miolo") or 0.0)) else "Recuo de fundo",
                        "observacao": "Pode zerar recuos de frente e laterais, desde que mantenha TO (máx) e TP (mín) da zona.",
                    }
                except Exception:
                    # Não quebra o app se algo falhar na montagem das opções do relatório
                    calc["opcao_1_recuos_padrao"] = None
                    calc["opcao_2_art112_sem_recuos_frente_laterais"] = None

    
            calc["pavimentos_estimados"] = estimate_pavimentos(g_pav, g_m)
    
        # =============================
        # Vagas (fallback antigo)
        # =============================
        vagas = None
        vagas_texto = None
        vagas_moto_txt = None

        # Regra específica: Residencial Unifamiliar NÃO exige vagas mínimas (Anexo IV - LC 90/2023)
        if use_code == "res_unifamiliar":
            vagas = 0
            vagas_texto = "Residencial unifamiliar: sem exigência mínima de vagas (Anexo IV — LC 90/2023)."
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
    
    
    def build_leigo_simulation(calc: Dict[str, Any], desired_total_area_m2: float, desired_pavimentos: int, area_util_m2: float) -> Dict[str, Any]:
        """
        Simulação “para leigo” para RES_UNI e RES_MULTI.
    
        Regras:
        - Se desired_total_area_m2 == 0: modo automático (mostra os limites máximos permitidos pela zona para a tipologia escolhida),
          respeitando simultaneamente IA e TO (considerando recuos).
        - Se desired_total_area_m2 > 0: modo projeto (checa se a proposta cabe em IA e TO).
        - Se desired_pavimentos == 0: usa pavimentos estimados (gabarito) ou 1.
        - area_util_m2 é opcional; se não informado, assume a área total usada (para vagas/sanitários).
        """
        rule = calc.get("rule") or {}
        area_lote = float(calc.get("area_lote") or 0)
    
        to_max = rule.get("to_max")
        tp_min = rule.get("tp_min")
        ia_max = rule.get("ia_max")
        ia_min = rule.get("ia_min")
    
        area_max_total = calc.get("area_max_total_construida")     # IA_total (m²)
        area_min_perm = calc.get("area_min_permeavel")             # TP_min (m²)
        area_max_ocup_real = calc.get("area_max_ocupacao_real")    # TO_real (m²) já considerando recuos quando possível
    
        pav_est = calc.get("pavimentos_estimados") or 1
        pav = int(desired_pavimentos or 0) if desired_pavimentos else int(pav_est or 1)
        pav = max(pav, 1)
    
        auto_mode = not (desired_total_area_m2 and float(desired_total_area_m2) > 0)
    
        # --- Define a área total usada (m²) ---
        if auto_mode:
            # Máximo permitido para a tipologia: min(IA_total, TO_real * pavimentos)
            candidates = []
            if isinstance(area_max_total, (int, float)) and area_max_total > 0:
                candidates.append(float(area_max_total))
            if isinstance(area_max_ocup_real, (int, float)) and area_max_ocup_real > 0:
                candidates.append(float(area_max_ocup_real) * pav)
            total_used = min(candidates) if candidates else 0.0
            total_mode = "máximo permitido (automático)"
            mode = "auto_limits"
        else:
            total_used = float(desired_total_area_m2)
            total_mode = "informado"
            mode = "project"
    
        # Pegada no térreo (aprox.) = área total / pavimentos
        footprint_proj = (total_used / pav) if pav > 0 else total_used
    
        # Área útil (para estacionamento/sanitários)
        if area_util_m2 and float(area_util_m2) > 0:
            area_util_used = float(area_util_m2)
            area_util_mode = "informada"
        else:
            area_util_used = float(total_used)
            area_util_mode = "automática (igual à área total usada)"
    
        # Percentuais úteis para leigo
        to_real_pct = (float(area_max_ocup_real) / area_lote) if (area_lote > 0 and isinstance(area_max_ocup_real, (int, float))) else None
        to_proj_pct = (float(footprint_proj) / area_lote) if area_lote > 0 else None
    
        # Checks
        has_to = to_max is not None and area_max_ocup_real is not None
        has_ia = ia_max is not None and area_max_total is not None
        has_tp = tp_min is not None and area_min_perm is not None
    
        ok_to = None
        if has_to:
            ok_to = float(footprint_proj) <= float(area_max_ocup_real) + 1e-9
    
        ok_ia = None
        if has_ia:
            ok_ia = float(total_used) <= float(area_max_total) + 1e-9
    
        # No modo automático, o "viável" significa: uso permitido (regra existe) + limites calculados.
        # No modo projeto, "viável" significa: cabe em TO e IA (quando houver).
        reasons = []
    
        if mode == "project":
            viable = True
            if has_ia and ok_ia is False:
                viable = False
                reasons.append("Área total construída acima do máximo permitido pelo IA.")
            if has_to and ok_to is False:
                viable = False
                reasons.append("Ocupação no térreo acima do máximo permitido pela TO (considerando recuos).")
            if not has_ia:
                reasons.append("IA máximo não cadastrado para esta combinação (não dá pra afirmar o máximo com segurança).")
            if not has_to:
                reasons.append("TO/recuos não cadastrados o suficiente para checar a ocupação no térreo com segurança.")
            if not has_tp:
                reasons.append("TP (permeabilidade mínima) não cadastrado o suficiente para calcular a área permeável.")
        else:
            viable = True
            if not has_ia:
                reasons.append("IA máximo não cadastrado (o limite total pode estar incompleto).")
            if not has_to:
                reasons.append("TO/recuos não cadastrados (o limite no térreo pode estar incompleto).")
            if not has_tp:
                reasons.append("TP (permeabilidade mínima) não cadastrado (não foi possível calcular a área permeável).")
    
        return {
            "mode": mode,
            "pavimentos_usados": pav,
            "total_used_m2": total_used,
            "total_mode": total_mode,
            "footprint_proj_m2": footprint_proj,
            "area_util_m2": area_util_used,
            "area_util_mode": area_util_mode,
            "to_real_pct": to_real_pct,
            "to_proj_pct": to_proj_pct,
            "limits": {
                "to_max_pct": to_max,
                "tp_min_pct": tp_min,
                "ia_min": ia_min,
                "ia_max": ia_max,
                "area_max_ocup_real_m2": area_max_ocup_real,
                "area_min_permeavel_m2": area_min_perm,
                "area_max_total_construida_m2": area_max_total,
            },
            "checks": {
                "has_to": has_to,
                "has_ia": has_ia,
                "has_tp": has_tp,
                "ok_to": ok_to,
                "ok_ia": ok_ia,
            },
            "viable": viable,
            "reasons": reasons,
        }
    
    
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
    
    preferred_order = ["Residencial", "Comercial", "Serviço", "Saúde/Educação", "Institucional", "Industrial", "Misto", "Sistema"]
    cats = sorted({(u.get("category") or "Sistema") for u in use_types}, key=lambda c: (preferred_order.index(c) if c in preferred_order else 999, c))
    
    st.sidebar.markdown("<div class='sidebar-section'><b>📋 1. Escolha o Uso</b></div>", unsafe_allow_html=True)
    
    cat_selected = st.sidebar.selectbox("Categoria:", cats, index=(cats.index("Residencial") if "Residencial" in cats else 0))
    
    in_cat = [u for u in use_types if (u.get("category") or "Sistema") == cat_selected]
    
    # Ordem amigável: no Residencial, Unifamiliar antes de Multifamiliar (e o resto por label)
    def _cat_sort_key(u: Dict[str, Any]):
        code = (u.get("code") or "").upper()
        label = (u.get("label") or "").lower()
        if cat_selected == "Residencial":
            if code.startswith("RES_UNI") or "unifamiliar" in label or "casa" in label:
                pri = 0
            elif code.startswith("RES_MULTI") or "multifamiliar" in label:
                pri = 1
            else:
                pri = 2
            return (pri, label)
        return (label,)
    
    in_cat = sorted(in_cat, key=_cat_sort_key)
    
    cat_options = {u["label"]: u["code"] for u in in_cat}
    if not cat_options:
        cat_options = {"Genérico (fallback) • Sistema": "SYS_FALLBACK"}
    
    use_label_cat = st.sidebar.selectbox("Opções na Categoria:", list(cat_options.keys()))
    use_code_cat = cat_options[use_label_cat]
    
    st.sidebar.markdown("<div class='sidebar-section' style='margin-top:10px;'><b>🔎 2. Busca Direta</b></div>", unsafe_allow_html=True)
    
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
    
        # Multifamiliar (opcional do motor antigo)
        qtd_unidades = None
        area_unidade_m2 = None
        if is_res_multi(use_code, use_label, use_category):
            st.subheader("Dados do multifamiliar (opcional • motor antigo)")
            qtd_u = st.number_input("Quantidade de apartamentos (opcional)", min_value=0, value=0, step=1)
            area_u = st.number_input("Área média do apartamento (m²) (opcional)", min_value=0.0, value=0.0, step=5.0)
            qtd_unidades = int(qtd_u) if qtd_u and qtd_u > 0 else None
            area_unidade_m2 = float(area_u) if area_u and area_u > 0 else None
    
        # Encostar (controlado pela regra + travado para multifamiliar por segurança)
        last_calc = st.session_state.get("calc") or {}
        last_rule = (last_calc.get("rule") or {}) if isinstance(last_calc, dict) else {}
        allow_attach_last = bool(last_rule.get("allow_attach_one_side") or False)
    
        disabled_attach = is_res_multi(use_code, use_label, use_category) or (not allow_attach_last)
    
        st.session_state["attach_one_side"] = st.checkbox(
            "Encostar em 1 lateral (zerar recuo)",
            value=bool(st.session_state.get("attach_one_side", False)),
            disabled=disabled_attach,
            help="Só habilita se a regra da zona permitir. Multifamiliar fica desabilitado por padrão (mais seguro)."
        )
    
        # =============================
        # Inputs complementares (Anexos III e IV - v2)
        # =============================
        park_v2_preview = sb_get_parking_rule_v2(use_code)
        base_metric = (park_v2_preview or {}).get("base_metric")
    
        st.subheader("Dados para vagas e sanitários")
    
        # Para deixar o fluxo mais clean:
        # - "Via local" só faz sentido para usos NÃO residenciais (dispensa ≤ 100m² em via local).
        # - VLT vira um ajuste opcional nos RESULTADOS (não aparece aqui).
        is_res_selected = is_res_any(use_code, use_label)
    
        near_vlt = False  # aplicado (opcional) nos resultados
    
        is_via_local = False
        if not is_res_selected:
            is_via_local = st.checkbox(
                "O imóvel está em via local? (dispensa não residencial ≤ 100m² área útil)",
                value=False
            )
    
        # defaults
        area_util_m2 = 0.0
        lugares = 0
        leitos = 0
        unidades_hospedagem = 0
        apartamentos = 0
        apto_area_m2 = 0.0
    
        # Para residencial, esconder esses dados em 'Opções avançadas' (evita confusão para leigo)
        if is_res_selected:
            with st.expander("Opções avançadas (opcional) • Dados para vagas/sanitários", expanded=False):
                area_util_m2 = st.number_input(
                    "Área útil (m²) (para vagas e sanitários)",
                    min_value=0.0,
                    value=0.0,
                    step=10.0
                )
    
                if base_metric == "lugares":
                    lugares = st.number_input("Quantidade de lugares", min_value=0, value=0, step=1)
                elif base_metric == "leitos":
                    leitos = st.number_input("Quantidade de leitos", min_value=0, value=0, step=1)
                elif base_metric == "unidades_hospedagem":
                    unidades_hospedagem = st.number_input("Unidades de hospedagem (UH)", min_value=0, value=0, step=1)
                elif base_metric in ("apartamentos",):
                    apartamentos = st.number_input("Quantidade de apartamentos (para estacionamento v2)", min_value=0, value=0, step=1)
                    apto_area_m2 = st.number_input(
                        "Área construída média do apartamento (m²) (para estacionamento v2)",
                        min_value=0.0,
                        value=0.0,
                        step=5.0
                    )
        else:
            # Não residencial: mostrar direto, porque pode ser obrigatório para calcular vagas/sanitários
            area_util_m2 = st.number_input(
                "Área útil (m²) (para vagas e sanitários)",
                min_value=0.0,
                value=0.0,
                step=10.0
            )
    
            if base_metric == "lugares":
                lugares = st.number_input("Quantidade de lugares", min_value=0, value=0, step=1)
            elif base_metric == "leitos":
                leitos = st.number_input("Quantidade de leitos", min_value=0, value=0, step=1)
            elif base_metric == "unidades_hospedagem":
                unidades_hospedagem = st.number_input("Unidades de hospedagem (UH)", min_value=0, value=0, step=1)
            elif base_metric in ("apartamentos",):
                apartamentos = st.number_input("Quantidade de apartamentos (para estacionamento v2)", min_value=0, value=0, step=1)
                apto_area_m2 = st.number_input(
                    "Área construída média do apartamento (m²) (para estacionamento v2)",
                    min_value=0.0,
                    value=0.0,
                    step=5.0
                )
    
        # =============================
        # Simulação “para leigo” (Residencial)
        # =============================
        desired_total_area_m2 = 0.0
        desired_pavimentos = 0
    
        if is_res_any(use_code, use_label, use_category):
            st.subheader("Simulação do projeto (para leigo)")
    
            # Unifamiliar: tipologia (térreo/duplex/triplex/outro) define pavimentos
            if is_res_uni(use_code, use_label, use_category):
                tip_options = ["Térreo", "Duplex", "Triplex", "Outro"]
                tip_default = st.session_state.get("res_uni_tipologia") or "Térreo"
                if tip_default not in tip_options:
                    tip_default = "Térreo"
                tipologia = st.selectbox("Tipo de residência", tip_options, index=tip_options.index(tip_default))
                st.session_state["res_uni_tipologia"] = tipologia
    
                if tipologia == "Outro":
                    desired_pavimentos = st.number_input("Quantos pavimentos?", min_value=1, value=1, step=1)
                else:
                    desired_pavimentos = {"Térreo": 1, "Duplex": 2, "Triplex": 3}[tipologia]
    
            # Multifamiliar: ainda permite informar pavimentos (ou usar gabarito/estimativa)
            else:
                desired_pavimentos = st.number_input(
                    "Pavimentos desejados — opcional (0 = usar gabarito/estimativa)",
                    min_value=0,
                    value=0,
                    step=1
                )
    
            desired_total_area_m2 = st.number_input(
                "Área construída TOTAL desejada (m²) — opcional (0 = usar o máximo permitido)",
                min_value=0.0,
                value=0.0,
                step=10.0
            )
    
        st.subheader("Calcular")
    
    
        if st.button("🚀 GERAR ESTUDO DE VIABILIDADE", use_container_width=True):
            with st.spinner("Calculando..."):
                res = compute_location(zone_index, ruas_index, lat, lon)
                st.session_state["res"] = res
    
                zona_sigla = res.get("zona_sigla") or ""
                rule = sb_get_zone_rule(zona_sigla, use_code)
    
                # estacionamento: v2 + fallback antigo
                park_v2 = sb_get_parking_rule_v2(use_code)
                park_old = sb_get_parking_rule(use_code)
    
                # sanitários
                use_prof = sb_get_use_sanitary_profile(use_code)
                san_prof = sb_get_sanitary_profile((use_prof or {}).get("sanitary_profile"))
    
                allow_attach_now = bool((rule or {}).get("allow_attach_one_side") or False)
                attach_one_side = bool(st.session_state.get("attach_one_side", False)) and allow_attach_now and (not is_res_multi(use_code, use_label, use_category))
    
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
                    park=park_old,  # fallback antigo
                    qtd_unidades=qtd_unidades,
                    area_unidade_m2=area_unidade_m2,
                )
    
                # store category + last simulation inputs (fix: possibilita recomputar/mostrar sempre)
                calc["use_category"] = use_category
                calc["_inputs_leigo"] = {
                    "desired_total_area_m2": float(desired_total_area_m2 or 0),
                    "desired_pavimentos": int(desired_pavimentos or 0),
                    "area_util_m2": float(area_util_m2 or 0),
                }
    
                # Simulação “para leigo” (Residencial Uni/Multi) — gera também uma área útil "automática"
                sim_leigo = None
                if is_res_any(use_code, use_label, use_category):
                    sim_leigo = build_leigo_simulation(
                        calc=calc,
                        desired_total_area_m2=float(desired_total_area_m2 or 0),
                        desired_pavimentos=int(desired_pavimentos or 0),
                        area_util_m2=float(area_util_m2 or 0),
                    )
                    calc["simulacao_leigo"] = sim
            # Opções de implantação (para o relatório)
            sim["options"] = {
                "padrao": calc.get("opcao_1_recuos_padrao"),
                "alinhamento_art112": calc.get("opcao_2_art112_sem_recuos_frente_laterais"),
            }
_leigo
                else:
                    calc["simulacao_leigo"] = None
    
                # Área útil efetiva para vagas/sanitários:
                # - se o usuário informou, usamos;
                # - se não informou e for residencial, assumimos a área do próprio estudo (simulação).
                effective_area_util = float(area_util_m2 or 0)
                if sim_leigo:
                    effective_area_util = float(sim_leigo.get("area_util_m2") or effective_area_util)
    
                calc["effective_area_util_m2"] = effective_area_util
    
                parking_inputs = {
                    "near_vlt": False,  # ajuste opcional nos resultados
                    "is_via_local": bool(is_via_local),
                    "area_util_m2": float(effective_area_util or 0),
                    "lugares": int(lugares or 0),
                    "leitos": int(leitos or 0),
                    "unidades_hospedagem": int(unidades_hospedagem or 0),
                    "apartamentos": int(apartamentos or 0),
                    "apto_area_m2": float(apto_area_m2 or 0),
                }
    
                if park_v2 and (park_v2.get("rule_json") or {}).get("use_code"):
                    pv2 = calc_parking_v2(park_v2["rule_json"], parking_inputs)
                    calc["parking_v2"] = pv2
                    calc["parking_v2_rule_json"] = park_v2["rule_json"]
                    calc["parking_v2_source_ref"] = park_v2.get("source_ref")
                else:
                    calc["parking_v2"] = None
                    calc["parking_v2_rule_json"] = None
                    calc["parking_v2_source_ref"] = None
    
                if san_prof and san_prof.get("rule_json") and float(effective_area_util or 0) > 0:
                    sres = calc_sanitary(san_prof["rule_json"], float(effective_area_util))
                    calc["sanitary"] = {
                        "profile": san_prof.get("sanitary_profile"),
                        "title": san_prof.get("title"),
                        "source_ref": san_prof.get("source_ref"),
                        "result": sres,
                    }
                else:
                    calc["sanitary"] = None
    
    
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
    
    # =============================
    # Viabilidade “para leigo” (Uni e Multi)
    # FIX: renderiza sempre que for residencial; se sim não existir, recalcula com inputs salvos.
    # =============================
    use_code_r = calc.get("use_code") or ""
    use_label_r = calc.get("use_label") or ""
    use_cat_r = calc.get("use_category") or use_category if "use_category" in globals() else ""
    
    if is_res_any(use_code_r, use_label_r, use_cat_r):
        sim = calc.get("simulacao_leigo")
        if not sim:
            # tenta recomputar com últimos inputs salvos
            last_in = (calc.get("_inputs_leigo") or {})
            sim = build_leigo_simulation(
                calc=calc,
                desired_total_area_m2=float(last_in.get("desired_total_area_m2") or 0),
                desired_pavimentos=int(last_in.get("desired_pavimentos") or 0),
                area_util_m2=float(last_in.get("area_util_m2") or 0),
            )
            calc["simulacao_leigo"] = sim
            # Opções de implantação (para o relatório)
            sim["options"] = {
                "padrao": calc.get("opcao_1_recuos_padrao"),
                "alinhamento_art112": calc.get("opcao_2_art112_sem_recuos_frente_laterais"),
            }

    
        st.divider()
    st.markdown("## ✅ Viabilidade (para leigo)")
    
    mode = sim.get("mode")
    lim = sim.get("limits") or {}
    checks = sim.get("checks") or {}
    
    # Vagas (prioriza v2)
    pv2 = calc.get("parking_v2")
    vagas_txt = "—"
    if pv2 and pv2.get("required") is not None:
        vagas_txt = str(int(pv2.get("required")))
    elif calc.get("vagas_min") is not None:
        vagas_txt = str(int(calc.get("vagas_min")))
    
    # Sanitários (totais)
    san = calc.get("sanitary") or {}
    san_totals = (san.get("result") or {}).get("totals") or {}
    san_txt = "—"
    if san_totals:
        san_txt = (
            f"Lavatórios {san_totals.get('lavatórios','—')} • "
            f"Aparelhos {san_totals.get('aparelhos_sanitários','—')} • "
            f"Mictórios {san_totals.get('mictórios','—')} • "
            f"Chuveiros {san_totals.get('chuveiros','—')}"
        )
    
    # Percentuais úteis
    to_real_pct = sim.get("to_real_pct")
    to_proj_pct = sim.get("to_proj_pct")
    
    # Status (Unifamiliar)
    if is_res_uni(calc.get("use_code"), calc.get("use_label")):
        if mode == "auto_limits":
            st.markdown(
                "<div class='ok'><b>✅ Uso permitido na zona</b><br/>Abaixo estão os limites máximos do lote (modo automático).</div>",
                unsafe_allow_html=True,
            )
        else:
            viable = bool(sim.get("viable"))
            status_html = (
                "<div class='ok'><b>✅ Seu projeto está dentro dos limites</b><br/>Pelos dados cadastrados, a proposta cabe na zona.</div>"
                if viable
                else "<div class='warn'><b>⚠️ Seu projeto ultrapassa algum limite</b><br/>Verifique TO (térreo) e/ou IA (total).</div>"
            )
            st.markdown(status_html, unsafe_allow_html=True)
    
        # Conteúdo (Unifamiliar) – texto bem leigo
        to_real_m2 = lim.get("area_max_ocup_real_m2")
        ia_max_m2 = lim.get("area_max_total_construida_m2")
        tp_min_m2 = lim.get("area_min_permeavel_m2")
    
        # máximo de área no térreo "do modo" (no automático, é o limite; no projeto, é a área do projeto no térreo)
        if mode == "auto_limits":
            terreo_m2 = to_real_m2
            terreo_pct = to_real_pct
            header_terreo = "No térreo você pode ocupar até"
            header_terreo_pct = "Isso corresponde a uma ocupação (TO real) de"
        else:
            terreo_m2 = sim.get("footprint_proj_m2")
            terreo_pct = to_proj_pct
            header_terreo = "Seu projeto ocupa no térreo (aprox.)"
            header_terreo_pct = "Isso corresponde a uma ocupação (TO do projeto) de"
    
        st.markdown(
            f"""
            <div class="card" style="margin-top:10px;">
              <h4>Resumo rápido</h4>
    
              <div class="muted">{header_terreo}</div>
              <div class="big">{fmt_m2(terreo_m2)}</div>
              <div class="muted">{header_terreo_pct}</div>
              <div class="big">{fmt_pct(terreo_pct) if terreo_pct is not None else "—"}</div>
    
              <hr style="margin:10px 0;" />
    
              <div class="muted">Você precisa deixar permeável (no mínimo)</div>
              <div class="big">{fmt_m2(tp_min_m2)}</div>
    
              <hr style="margin:10px 0;" />
    
              <div class="muted">Você pode construir no total (no máximo)</div>
              <div class="big">{fmt_m2(ia_max_m2)}</div>
    
              <hr style="margin:10px 0;" />
    
              <div class="muted">Tipologia / pavimentos usados</div>
              <div class="big">{int(sim.get("pavimentos_usados") or 1)} pav</div>
    
              <div class="muted" style="margin-top:8px;">Área total usada na conta</div>
              <div class="big">{fmt_m2(sim.get("total_used_m2"))} <span class="muted">({sim.get("total_mode")})</span></div>
    
              <div class="muted" style="margin-top:8px;">Área útil (vagas/sanitários)</div>
              <div class="big">{fmt_m2(sim.get("area_util_m2"))} <span class="muted">({sim.get("area_util_mode")})</span></div>
    
              <hr style="margin:10px 0;" />
    
              <div class="muted">Vagas mínimas</div>
              <div class="big">{vagas_txt}</div>
    
              <div class="muted" style="margin-top:8px;">Sanitários mínimos (se houver perfil)</div>
              <div class="big" style="font-size:16px;">{san_txt}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    
        # Detalhes (comparações) só quando o usuário informou área
        if mode == "project":
            with st.expander("Detalhes da checagem (TO / IA)"):
                # TO
                if checks.get("has_to"):
                    st.write(f"- TO (térreo): {fmt_m2(sim.get('footprint_proj_m2'))} (projeto) vs {fmt_m2(lim.get('area_max_ocup_real_m2'))} (máximo c/ recuos) → " +
                             ("✅ ok" if checks.get("ok_to") else "⚠️ excede"))
                else:
                    st.write("- TO: **sem dados suficientes** (TO/recuos não cadastrados).")
    
                # IA
                if checks.get("has_ia"):
                    st.write(f"- IA (total): {fmt_m2(sim.get('total_used_m2'))} (projeto) vs {fmt_m2(lim.get('area_max_total_construida_m2'))} (máximo) → " +
                             ("✅ ok" if checks.get("ok_ia") else "⚠️ excede"))
                else:
                    st.write("- IA: **sem dados suficientes** (IA máximo não cadastrado).")
    
        # Observações gerais (se faltam índices)
        reasons = sim.get("reasons") or []
        if reasons:
            with st.expander("Observações"):
                for r in reasons:
                    st.write(f"- {r}")
        # =============================
        # Relatório completo (Markdown)
        # =============================
        report_md = build_unifamiliar_report_md(res=res, calc=calc, sim=sim)
        with st.expander("📄 Relatório urbanístico completo (Residencial Unifamiliar)"):
            st.markdown(report_md)
            st.download_button(
                "⬇️ Baixar relatório (.md)",
                data=report_md,
                file_name="RELATORIO_UNIFAMILIAR.md",
                mime="text/markdown",
                use_container_width=True,
            )


if __name__ == "__main__":
    main()
