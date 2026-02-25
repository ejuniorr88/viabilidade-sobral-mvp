import streamlit as st
from core.zone_rules_repository import get_zone_rule
from core.utils_format import fmt_pct, fmt_m2

def render_unifamiliar():
    st.title("Viabilidade – Residencial Unifamiliar")

    zone_sigla = st.selectbox("Zona", ["ZAM", "ZAP", "ZOP"])
    use_code = "RES_UNI"

    area_lote = st.number_input("Área do lote (m²)", min_value=1.0, value=300.0)
    area_usuario = st.number_input("Área pretendida no térreo (m²)", min_value=0.0, value=0.0)

    rule = get_zone_rule(zone_sigla, use_code)

    if not rule:
        st.error("Regra não encontrada no banco.")
        return

    to_max = rule["to_max"]
    tp_min = rule["tp_min"]
    ia_max = rule["ia_max"]

    recuo_frontal = rule.get("recuo_frontal_m", 0) or 0
    recuo_lateral = rule.get("recuo_lateral_m", 0) or 0
    recuo_fundos = rule.get("recuo_fundos_m", 0) or 0

    allow_zero = rule.get("allow_zero_front_lateral", False)

    area_max_to = area_lote * to_max
    area_min_tp = area_lote * tp_min
    area_max_ia = area_lote * ia_max

    largura_lote = 10
    profundidade_lote = 30

    largura_util = largura_lote - (2 * recuo_lateral)
    profundidade_util = profundidade_lote - recuo_frontal - recuo_fundos

    if largura_util > 0 and profundidade_util > 0:
        area_recuos = largura_util * profundidade_util
        max_terreo_padrao = min(area_max_to, area_recuos)
    else:
        max_terreo_padrao = None

    if allow_zero:
        largura_art112 = largura_lote
        profundidade_art112 = profundidade_lote - recuo_fundos
        area_art112 = largura_art112 * profundidade_art112
        max_terreo_art112 = min(area_max_to, area_art112)
    else:
        max_terreo_art112 = None

    area_base = area_usuario if area_usuario > 0 else max_terreo_padrao

    st.subheader("Quadro técnico final (consolidado)")

    st.markdown(f"""
    - **Zona:** {zone_sigla}
    - **Área do lote:** {fmt_m2(area_lote)}
    - **TO máx:** {fmt_pct(to_max)} (≈ {fmt_m2(area_max_to)} no térreo)
    - **TP mín:** {fmt_pct(tp_min)} (≈ {fmt_m2(area_min_tp)} permeável)
    - **IA máx:** {ia_max} (≈ {fmt_m2(area_max_ia)} total)
    - **Máx. térreo (recuos padrão):** {fmt_m2(max_terreo_padrao)}
    - **Máx. térreo (Art. 112):** {fmt_m2(max_terreo_art112)}
    """)
