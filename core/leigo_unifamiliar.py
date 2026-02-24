"""Módulo Unifamiliar (RES_UNI) — Viabilidade para leigo.

Este arquivo concentra a lógica de apresentação do bloco 'Viabilidade (para leigo)'
para Residencial Unifamiliar, mantendo o app principal mais leve.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import streamlit as st


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


def render_unifamiliar_leigo(calc: Dict[str, Any], sim: Dict[str, Any]) -> None:
    """Renderiza a seção '✅ Viabilidade (para leigo)' para Residencial Unifamiliar.

    - Modo automático (área=0): mostra limites do lote e afirma 'uso permitido na zona'.
    - Modo projeto (área>0): mostra TO do projeto (m² e %) e checagens.
    """
    # Dependências do app (CSS .card, .pill, .ok, .warn já são definidos no app principal)
    lim = (sim or {}).get("limits") or {}
    area_lote = float((calc or {}).get("area_lote") or 0)

    # Descobre se foi modo automático pelo "total_proj_mode"
    total_mode = (sim or {}).get("total_proj_mode") or ""
    is_auto = ("máximo" in total_mode.lower()) or ("automatic" in total_mode.lower())

    to_real = lim.get("area_max_ocup_real_m2")
    ia_total = lim.get("area_max_total_construida_m2")
    tp_min = lim.get("area_min_permeavel_m2")

    # TO real em % do lote (considerando recuos)
    to_real_pct = (float(to_real) / area_lote) if (to_real is not None and area_lote > 0) else None

    if is_auto:
        st.markdown("<div class='ok'><b>✅ Uso permitido na zona</b><br/>Abaixo estão os limites máximos do lote (modo automático).</div>", unsafe_allow_html=True)

        st.markdown(
            f"""
            <div class="card" style="margin-top:10px;">
              <h4>Limites do lote (Térreo)</h4>

              <div class="muted">Ocupação máxima no térreo (já com recuos)</div>
              <div class="big">{_fmt_m2(to_real)}</div>
              <div class="muted">Isso corresponde a (aprox.)</div>
              <div class="big">{_fmt_pct(to_real_pct)}</div>

              <hr style="margin:10px 0;" />

              <div class="muted">Permeabilidade mínima exigida</div>
              <div class="big">{_fmt_m2(tp_min)}</div>

              <hr style="margin:10px 0;" />

              <div class="muted">Área total máxima (IA)</div>
              <div class="big">{_fmt_m2(ia_total)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    # Modo projeto (área informada)
    viable = bool((sim or {}).get("viable"))
    ok_to = (sim or {}).get("checks", {}).get("ok_to")
    ok_ia = (sim or {}).get("checks", {}).get("ok_ia")

    status_html = (
        "<div class='ok'><b>✅ Dentro dos limites</b><br/>Pelo que foi informado, seu projeto está dentro de TO e IA.</div>"
        if viable
        else "<div class='warn'><b>⚠️ Atenção</b><br/>Seu projeto excede TO e/ou IA (ver detalhes abaixo).</div>"
    )
    st.markdown(status_html, unsafe_allow_html=True)

    footprint = (sim or {}).get("footprint_proj_m2")
    total_proj = (sim or {}).get("total_proj_m2")

    # TO do projeto (% do lote)
    to_proj_pct = (float(footprint) / area_lote) if (footprint is not None and area_lote > 0) else None

    st.markdown(
        f"""
        <div class="card" style="margin-top:10px;">
          <h4>Seu projeto (Térreo)</h4>

          <div class="muted">Ocupação do projeto no térreo</div>
          <div class="big">{_fmt_m2(footprint)}</div>
          <div class="muted">TO do projeto (aprox.)</div>
          <div class="big">{_fmt_pct(to_proj_pct)}</div>

          <div class="muted" style="margin-top:8px;">Máximo permitido no térreo (com recuos)</div>
          <div class="big">{_fmt_m2(to_real)} {'✅' if ok_to else '⚠️' if ok_to is False else ''}</div>

          <hr style="margin:10px 0;" />

          <div class="muted">Área total construída informada</div>
          <div class="big">{_fmt_m2(total_proj)}</div>
          <div class="muted">Máximo permitido no total (IA)</div>
          <div class="big">{_fmt_m2(ia_total)} {'✅' if ok_ia else '⚠️' if ok_ia is False else ''}</div>

          <hr style="margin:10px 0;" />

          <div class="muted">Permeabilidade mínima exigida</div>
          <div class="big">{_fmt_m2(tp_min)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    reasons = (sim or {}).get("reasons") or []
    if reasons:
        with st.expander("Detalhes / Observações"):
            for r in reasons:
                st.write(f"- {r}")
