"""Módulo Unifamiliar (RES_UNI) — Viabilidade para leigo.

Inclui:
- Opção 1: Recuos padrão (regras da zona)
- Opção 2: Alinhamento (Art. 112 – LC 90/2023): pode zerar recuos frontal e laterais (fundo permanece)
- Permeabilidade mínima e tabela de pisos (Art. 108 – LC 90/2023)
- Estacionamento: não exigido para unifamiliar (Anexo IV – LC 90/2023)
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import streamlit as st


PISOS_PERMEABILIDADE = [
    ("Grama", "100%"),
    ("Brita solta / terra batida", "100%"),
    ("Piso drenante", "90%"),
    ("Bloco de concreto vazado (piso verde)", "60%"),
    ("Pedra portuguesa / intertravado", "25%"),
]


def _fmt_pct(x: Optional[float]) -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x) * 100:.1f}%"
    except Exception:
        return "—"


def _fmt_m2(x: Optional[float]) -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x):.2f} m²"
    except Exception:
        return "—"


def _fmt_m(x: Optional[float]) -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x):.2f} m"
    except Exception:
        return "—"


def _tipologia_label(pav: int) -> str:
    if pav <= 1:
        return "Térreo"
    if pav == 2:
        return "Duplex"
    if pav == 3:
        return "Triplex"
    return f"Outro ({pav} pavimentos)"


def render_unifamiliar_leigo(calc: Dict[str, Any], sim: Dict[str, Any]) -> None:
    lim = (sim or {}).get("limits") or {}
    options = (sim or {}).get("options") or {}
    area_lote = float((calc or {}).get("area_lote") or 0)

    pav = int((sim or {}).get("pavimentos_usados") or 1)
    pav = max(pav, 1)
    tipologia = _tipologia_label(pav)

    total_mode = (sim or {}).get("total_proj_mode") or ""
    is_auto = "máximo" in total_mode.lower()

    to_pct = lim.get("to_max_pct")
    tp_pct = lim.get("tp_min_pct")
    ia_max = lim.get("ia_max")
    tp_min = lim.get("area_min_permeavel_m2")

    vagas_txt = "Não exigido (Anexo IV)"
    # Sanitários (geralmente não aplicável para residência unifamiliar)
    san_txt = "—"
    san = (calc or {}).get("sanitary") or {}
    san_totals = ((san.get("result") or {}).get("totals") or {})
    if san_totals:
        san_txt = (
            f"Lavatórios {san_totals.get('lavatórios','—')} • "  # noqa
            f"Aparelhos {san_totals.get('aparelhos_sanitários','—')} • "  # noqa
            f"Mictórios {san_totals.get('mictórios','—')} • "  # noqa
            f"Chuveiros {san_totals.get('chuveiros','—')}"
        )

    def _render_option_card(key: str):
        opt = options.get(key) or {}
        if not opt:
            return
        area_max_terreo = opt.get("area_max_terreo_m2")
        area_miolo = opt.get("area_miolo_m2")

        to_eff = (float(area_max_terreo) / area_lote) if (area_max_terreo and area_lote > 0) else None

        st.markdown(
            f"""
            <div class="card" style="margin-top:10px;">
              <h4>{opt.get("label")}</h4>

              <div class="muted">Recuos considerados</div>
              <div class="big" style="font-size:16px;">
                Frente: {_fmt_m(opt.get("recuo_frontal_m"))} •
                Laterais: {_fmt_m(opt.get("recuo_lateral_m"))} •
                Fundo: {_fmt_m(opt.get("recuo_fundos_m"))}
              </div>

              <div class="muted" style="margin-top:8px;">Área interna disponível (miolo)</div>
              <div class="big">{_fmt_m2(area_miolo)}</div>

              <div class="muted" style="margin-top:8px;">Máximo no térreo (respeitando TO)</div>
              <div class="big">{_fmt_m2(area_max_terreo)} <span class="muted">(~{_fmt_pct(to_eff)})</span></div>

              <div class="muted" style="margin-top:6px;">Limitador</div>
              <div class="big" style="font-size:14px;">{opt.get("motivo_limitador") or "—"}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if opt.get("legal_ref"):
            st.caption(f"Base: {opt.get('legal_ref')}")

    if is_auto:
        st.markdown(
            "<div class='ok'><b>✅ Uso permitido na zona</b><br/>Abaixo estão os limites e o máximo viável para a tipologia escolhida (modo automático).</div>",
            unsafe_allow_html=True,
        )

        total_max_viavel = float((sim or {}).get("total_proj_m2") or 0)

        opt2 = options.get("alinhamento_art112") or {}
        opt1 = options.get("padrao") or {}
        terreo_max = opt2.get("area_max_terreo_m2") if opt2.get("area_max_terreo_m2") is not None else opt1.get("area_max_terreo_m2")
        terreo_max = float(terreo_max or 0)

        resto = max(total_max_viavel - terreo_max, 0.0)
        por_pav_superior = (resto / (pav - 1)) if pav > 1 else 0.0

        if pav == 1:
            dist_txt = f"Térreo até {_fmt_m2(terreo_max)}"
        elif pav == 2:
            dist_txt = f"Térreo até {_fmt_m2(terreo_max)} • Superior até {_fmt_m2(resto)}"
        else:
            dist_txt = f"Térreo até {_fmt_m2(terreo_max)} • Superiores ~ {_fmt_m2(por_pav_superior)} cada"

        st.markdown(
            f"""
            <div class="card" style="margin-top:10px;">
              <h4>Resumo rápido (Unifamiliar • {tipologia})</h4>

              <div class="muted">TO máxima da zona</div>
              <div class="big">{_fmt_pct(to_pct)}</div>

              <div class="muted">TP mínima (permeabilidade)</div>
              <div class="big">{_fmt_pct(tp_pct)} • {_fmt_m2(tp_min)}</div>

              <div class="muted">IA máximo</div>
              <div class="big">{ia_max if ia_max is not None else "—"} • Máx. total {_fmt_m2(lim.get("area_max_total_construida_m2"))}</div>

              <hr style="margin:10px 0;" />

              <div class="muted">Máximo viável no total (IA + TO)</div>
              <div class="big">{_fmt_m2(total_max_viavel)} <span class="muted">({(sim or {}).get("total_proj_mode")})</span></div>

              <div class="muted" style="margin-top:6px;">Distribuição sugerida</div>
              <div class="big" style="font-size:16px;">{dist_txt}</div>

              <hr style="margin:10px 0;" />

              <div class="muted">Estacionamento</div>
              <div class="big" style="font-size:16px;">{vagas_txt}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown("### 📌 Opções de implantação")
        _render_option_card("padrao")
        if options.get("alinhamento_art112"):
            _render_option_card("alinhamento_art112")

        with st.expander("🌿 Permeabilidade — como a lei conta os pisos (Art. 108)"):
            st.write("Nem todo piso externo conta 100% como área permeável. Exemplos:")
            st.table(PISOS_PERMEABILIDADE)
            st.caption("Base: LC 90/2023, Art. 108")

        return

    # MODO PROJETO
    viable = bool((sim or {}).get("viable"))
    ok_to = (sim or {}).get("checks", {}).get("ok_to")
    ok_ia = (sim or {}).get("checks", {}).get("ok_ia")

    status_html = (
        "<div class='ok'><b>✅ Projeto dentro dos limites</b><br/>Pelo que foi informado, seu projeto está dentro de TO e IA.</div>"
        if viable
        else "<div class='warn'><b>⚠️ Projeto excede algum limite</b><br/>Seu projeto excede TO e/ou IA (ver detalhes abaixo).</div>"
    )
    st.markdown(status_html, unsafe_allow_html=True)

    footprint = (sim or {}).get("footprint_proj_m2")
    total_proj = (sim or {}).get("total_proj_m2")
    to_proj_pct = (float(footprint) / area_lote) if (footprint is not None and area_lote > 0) else None

    terreo_limit = (options.get("padrao") or {}).get("area_max_terreo_m2")

    st.markdown(
        f"""
        <div class="card" style="margin-top:10px;">
          <h4>Seu projeto (Unifamiliar • {tipologia})</h4>

          <div class="muted">Ocupação do projeto no térreo</div>
          <div class="big">{_fmt_m2(footprint)}</div>
          <div class="muted">TO do seu projeto (aprox.)</div>
          <div class="big">{_fmt_pct(to_proj_pct)}</div>

          <div class="muted" style="margin-top:8px;">Máximo permitido no térreo (recuos padrão)</div>
          <div class="big">{_fmt_m2(terreo_limit)} {'✅' if ok_to else '⚠️' if ok_to is False else ''}</div>

          <hr style="margin:10px 0;" />

          <div class="muted">Área total construída informada</div>
          <div class="big">{_fmt_m2(total_proj)}</div>
          <div class="muted">Máximo permitido no total (IA)</div>
          <div class="big">{_fmt_m2(lim.get("area_max_total_construida_m2"))} {'✅' if ok_ia else '⚠️' if ok_ia is False else ''}</div>

          <hr style="margin:10px 0;" />

          <div class="muted">Permeabilidade mínima exigida</div>
          <div class="big">{_fmt_m2(tp_min)}</div>

          <hr style="margin:10px 0;" />

          <div class="muted">Estacionamento</div>
          <div class="big" style="font-size:16px;">{vagas_txt}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("### 📌 Opções de implantação (referência)")
    _render_option_card("padrao")
    if options.get("alinhamento_art112"):
        _render_option_card("alinhamento_art112")

    with st.expander("🌿 Permeabilidade — como a lei conta os pisos (Art. 108)"):
        st.write("Nem todo piso externo conta 100% como área permeável. Exemplos:")
        st.table(PISOS_PERMEABILIDADE)
        st.caption("Base: LC 90/2023, Art. 108")

    reasons = (sim or {}).get("reasons") or []
    if reasons:
        with st.expander("Detalhes / Observações"):
            for r in reasons:
                st.write(f"- {r}")
