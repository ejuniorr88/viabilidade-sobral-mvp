"""Relatório Urbanístico — Residencial Unifamiliar (Markdown)."""

from __future__ import annotations

from typing import Any, Dict, Optional


def _fmt_pct(x: Optional[float]) -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x) * 100:.1f}%"
    except Exception:
        return "—"


def _fmt_m(x: Optional[float]) -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x):.2f} m"
    except Exception:
        return "—"


def _fmt_m2(x: Optional[float]) -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x):.2f} m²"
    except Exception:
        return "—"


PISOS_PERMEABILIDADE = [
    ("Grama", "100%"),
    ("Brita solta / terra batida", "100%"),
    ("Piso drenante", "90%"),
    ("Bloco de concreto vazado (piso verde)", "60%"),
    ("Pedra portuguesa / intertravado", "25%"),
]


AMBIENTES_ANEXO_II = [
    ("Sala de estar", "2,00 m", "8,00 m²", "1/8", "1/12", "2,50 m", "7"),
    ("Sala de jantar", "2,00 m", "6,00 m²", "1/8", "1/12", "2,50 m", "7"),
    ("Cozinha", "1,80 m", "5,00 m²", "1/8", "1/12", "2,50 m", "1-7"),
    ("1º e 2º quartos", "2,00 m", "8,00 m²", "1/8", "1/12", "2,50 m", "—"),
    ("Demais quartos", "2,00 m", "5,00 m²", "1/8", "1/12", "2,50 m", "—"),
    ("Banheiro", "1,00 m", "1,50 m²", "1/10", "1/16", "2,20 m", "1-2-3"),
    ("Área de serviço", "1,20 m", "1,80 m²", "1/10", "1/16", "2,20 m", "1-2-7"),
    ("Garagem", "2,20 m", "9,00 m²", "1/14", "1/24", "2,20 m", "7"),
    ("Escada", "0,80 m", "—", "—", "—", "2,10 m", "8-11-12-13"),
]


OBS_ANEXO_II = [
    "Tolera-se iluminação e ventilação zenital.",
    "Admite-se ventilação mecânica ou indireta nos casos permitidos.",
    "Banheiro não pode comunicar-se diretamente com cozinha ou sala de jantar.",
    "Corredores com mais de 5,00m devem ter largura mínima de 1,00m.",
    "Corredores com mais de 10,00m exigem ventilação mínima proporcional.",
    "Área de porta com veneziana pode ser computada como ventilação.",
    "Escadas devem ser de material incombustível ou tratado.",
    "Patamar obrigatório quando houver mudança de direção ou altura superior a 2,90m.",
    "Largura mínima do degrau: 0,25m.",
    "Altura máxima do degrau: 0,19m.",
]


def build_unifamiliar_report_md(res: Dict[str, Any], calc: Dict[str, Any], sim: Dict[str, Any]) -> str:
    area_lote = float(calc.get("area_lote") or 0)
    testada = float(calc.get("testada") or 0)
    profundidade = float(calc.get("profundidade") or 0)

    zona = res.get("zona_sigla") or "—"
    zona_nome = res.get("zona_nome") or "—"
    tipo = "Esquina" if calc.get("esquina") else "Meio de quadra"

    lim = (sim or {}).get("limits") or {}
    options = (sim or {}).get("options") or {}

    to_pct = lim.get("to_max_pct")
    tp_pct = lim.get("tp_min_pct")
    ia_max = lim.get("ia_max")
    ia_total = lim.get("area_max_total_construida_m2")
    tp_min = lim.get("area_min_permeavel_m2")

    op1 = options.get("padrao") or {}
    op2 = options.get("alinhamento_art112") or {}

    to_teor = (float(to_pct) * area_lote) if to_pct is not None else None

    if to_teor is not None:
        area_restante = max(area_lote - to_teor, 0.0)
        area_imper = max(area_restante - float(tp_min or 0), 0.0)
    else:
        area_restante = None
        area_imper = None

    total_proj = sim.get("total_proj_m2")
    total_mode = sim.get("total_proj_mode")
    pav = int(sim.get("pavimentos_usados") or 1)
    tipologia = "Térreo" if pav == 1 else ("Duplex" if pav == 2 else ("Triplex" if pav == 3 else f"Outro ({pav} pavimentos)"))

    md = []
    md.append("# 🏡 RELATÓRIO URBANÍSTICO\nResidencial Unifamiliar\n\n")
    md.append(f"**Terreno:** {_fmt_m2(area_lote)}\n")
    md.append(f"**Dimensões:** {testada:.2f} m × {profundidade:.2f} m\n")
    md.append(f"**Zona:** {zona} — {zona_nome}\n")
    md.append(f"**Tipo:** {tipo}\n")
    md.append(f"**Tipologia simulada:** {tipologia} ({pav} pavimentos)\n")
    md.append("\n---\n\n")

    md.append("## 📍 1️⃣ Quanto posso ocupar no chão?\n\n")
    md.append("**Pergunta:** Quanto posso ocupar no térreo?\n\n")
    md.append(f"**Resposta:** A zona permite ocupar até **{_fmt_pct(to_pct)}** do lote.\n\n")
    md.append("**Cálculo demonstrado:**\n")
    md.append(f"- {_fmt_m2(area_lote)} × {_fmt_pct(to_pct)} = **{_fmt_m2(to_teor)}**\n\n")
    md.append("**Explicação didática:** Esse é o limite máximo pela **Taxa de Ocupação (TO)**.\n\n")
    md.append("Agora veja duas situações possíveis:\n\n")

    if op1 is not None:
        md.append("✅ **Opção 1 — Recuos padrão**\n\n")
        md.append("Recuos considerados:\n")
        md.append(f"- Frontal: {_fmt_m(op1.get('recuo_frontal_m'))}\n")
        md.append(f"- Laterais: {_fmt_m(op1.get('recuo_lateral_m'))}\n")
        md.append(f"- Fundo: {_fmt_m(op1.get('recuo_fundos_m'))}\n\n")
        md.append("Área interna disponível (miolo):\n")
        md.append(f"- **{_fmt_m2(op1.get('area_miolo_m2'))}**\n\n")
        md.append("Máximo no térreo (TO + recuos):\n")
        md.append(f"- **{_fmt_m2(op1.get('area_max_terreo_m2'))}**\n\n")
        md.append("> Mesmo podendo ocupar pela TO, o **miolo** (recuos) pode reduzir o máximo real.\n\n")

    if op2 is not None:
        md.append("✅ **Opção 2 — Implantação no alinhamento (Art. 112 – LC 90/2023)**\n\n")
        md.append("Por ser residência unifamiliar, a legislação permite **zerar recuos frontal e laterais**, desde que respeite TO e TP.\n\n")
        md.append("Recuos considerados:\n")
        md.append("- Frontal: 0,00 m\n")
        md.append("- Laterais: 0,00 m\n")
        md.append(f"- Fundo: {_fmt_m(op2.get('recuo_fundos_m'))} (permanece obrigatório)\n\n")
        md.append("Área interna disponível (miolo):\n")
        md.append(f"- **{_fmt_m2(op2.get('area_miolo_m2'))}**\n\n")
        md.append("Máximo no térreo (respeitando TO):\n")
        md.append(f"- **{_fmt_m2(op2.get('area_max_terreo_m2'))}**\n\n")


        # --- Projeto do usuário (se informado) ---
        try:
            total_proj_val = float(total_proj) if total_proj is not None else 0.0
        except Exception:
            total_proj_val = 0.0
        if total_proj_val and pav:
            # estimativa simples: dividir igualmente por pavimentos
            area_terreo_proj = total_proj_val / max(1, int(pav))
            to_real_proj = area_terreo_proj / area_lote if area_lote else 0.0
            area_restante_proj = max(0.0, area_lote - area_terreo_proj) if area_lote else 0.0
            area_imper_proj = max(0.0, area_restante_proj - tp_min) if tp_min is not None else None

            md.append("### ✅ Se você informou uma área para o seu projeto

")
            md.append(f"- Área total informada: **{_fmt_m2(total_proj_val)}** em **{pav}** pavimentos (estimativa: pavimentos iguais)\n")
            md.append(f"- Área estimada no térreo: **{_fmt_m2(area_terreo_proj)}**\n")
            md.append(f"- Isso dá uma **TO do seu projeto** de **{_fmt_pct(to_real_proj)}**\n\n")

            if tp_min is not None:
                md.append("**Permeabilidade (TP) com a sua área no térreo:**\n")
                md.append(f"- Área restante no lote: {_fmt_m2(area_lote)} − {_fmt_m2(area_terreo_proj)} = **{_fmt_m2(area_restante_proj)}**\n")
                md.append(f"- Desses, **{_fmt_m2(tp_min)}** devem ser permeáveis (solo)\n")
                if area_imper_proj is not None:
                    md.append(f"- O restante (**{_fmt_m2(area_imper_proj)}**) pode ser piso impermeável\n\n")

            # checagem simples contra os máximos de cada opção (se disponíveis)
            if op1 is not None and op1.get("area_max_terreo_m2") is not None:
                ok1 = area_terreo_proj <= float(op1.get("area_max_terreo_m2") or 0)
                md.append(f"- Checagem (recuos padrão): seu térreo {_fmt_m2(area_terreo_proj)} vs máx {_fmt_m2(op1.get('area_max_terreo_m2'))} → **{'OK' if ok1 else 'ULTRAPASSA'}**\n")
            if op2 is not None and op2.get("area_max_terreo_m2") is not None:
                ok2 = area_terreo_proj <= float(op2.get("area_max_terreo_m2") or 0)
                md.append(f"- Checagem (Art. 112): seu térreo {_fmt_m2(area_terreo_proj)} vs máx {_fmt_m2(op2.get('area_max_terreo_m2'))} → **{'OK' if ok2 else 'ULTRAPASSA'}**\n")
            md.append("\n")
    md.append("---\n\n")
    md.append("## 🌿 2️⃣ Quanto preciso deixar livre?\n\n")
    md.append("**Pergunta:** Quanto preciso deixar permeável?\n\n")
    md.append(f"**Resposta:** A zona exige **{_fmt_pct(tp_pct)}** de área permeável.\n\n")
    md.append("**Cálculo demonstrado:**\n")
    md.append(f"- {_fmt_m2(area_lote)} × {_fmt_pct(tp_pct)} = **{_fmt_m2(tp_min)}**\n\n")

    if to_teor is not None:
        md.append("**Exemplo didático (se você ocupar o máximo no térreo pela TO):**\n")
        md.append(f"- Área restante: {_fmt_m2(area_lote)} − {_fmt_m2(to_teor)} = **{_fmt_m2(area_restante)}**\n")
        md.append(f"- Desses, **{_fmt_m2(tp_min)}** devem ser permeáveis (solo).\n")
        md.append(f"- O restante (**{_fmt_m2(area_imper)}**) pode ser piso impermeável.\n\n")

    md.append("**Importante:** nem todo piso conta 100% como permeável (Art. 108).\n\n")
    md.append("| Tipo de piso | % considerado permeável |\n|---|---:|\n")
    for name, pct in PISOS_PERMEABILIDADE:
        md.append(f"| {name} | {pct} |\n")
    md.append("\n")

    md.append("---\n\n")
    md.append("## 🏢 3️⃣ Posso construir mais andares?\n\n")
    md.append("**Pergunta:** Qual o limite total de área construída?\n\n")
    md.append(f"**Resposta:** IA máximo **{ia_max if ia_max is not None else '—'}**, total máximo **{_fmt_m2(ia_total)}**.\n\n")
    md.append("**Cálculo demonstrado:**\n")
    md.append(f"- {_fmt_m2(area_lote)} × {ia_max if ia_max is not None else '—'} = **{_fmt_m2(ia_total)}**\n\n")
    md.append(f"**Simulação usada:** {_fmt_m2(total_proj)} ({total_mode}) com {pav} pavimentos.\n\n")

    md.append("---\n\n")
    md.append("## 🚗 4️⃣ Estacionamento\n\n")
    md.append("**Pergunta:** Precisa de vagas mínimas?\n\n")
    md.append("**Resposta:** Para residência unifamiliar, **não há exigência mínima de vagas** (Anexo IV – LC 90/2023).\n\n")

    md.append("---\n\n")
    md.append("## 🧾 QUADRO TÉCNICO – PARÂMETROS DOS AMBIENTES (Anexo II – LC 90/2023)\n\n")
    md.append("| Ambiente | Círculo inscrito | Área mínima | Iluminação | Ventilação | Pé-direito | Obs. |\n|---|---:|---:|---:|---:|---:|---|\n")
    for row in AMBIENTES_ANEXO_II:
        md.append("| " + " | ".join(row) + " |\n")
    md.append("\n**Observações (Anexo II):**\n")
    for obs in OBS_ANEXO_II:
        md.append(f"- {obs}\n")

    md.append("\n---\n\n")
    md.append("### Quadro técnico final (consolidado)\n\n")
    md.append(f"- Zona: {zona}\n")
    md.append(f"- Área do lote: {_fmt_m2(area_lote)}\n")
    md.append(f"- TO máx: {_fmt_pct(to_pct)} (≈ {_fmt_m2(to_teor)} no térreo)\n")
    md.append(f"- TP mín: {_fmt_pct(tp_pct)} (≈ {_fmt_m2(tp_min)} permeável)\n")
    md.append(f"- IA máx: {ia_max if ia_max is not None else '—'} (≈ {_fmt_m2(ia_total)} total)\n")
    if op1 is not None:
        md.append(f"- Máx. térreo (recuos padrão): {_fmt_m2(op1.get('area_max_terreo_m2'))}\n")
    if op2 is not None:
        md.append(f"- Máx. térreo (Art. 112): {_fmt_m2(op2.get('area_max_terreo_m2'))}\n")

    return "".join(md)
