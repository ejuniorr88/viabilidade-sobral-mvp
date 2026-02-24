# 🏙️ Projeto: VIABILIDADE MVP – Sistema de Análise Urbanística

## 🎯 Objetivo

Aplicação web em Streamlit para análise automatizada de viabilidade urbana,
baseada no:

- LC 90/2023 – Código de Ordenamento Urbano
- LC 91/2023 – Parcelamento, Uso e Ocupação do Solo
- LC 92/2023 – Plano Diretor
- Lei 2416 – Sistema Viário
- Lei 2417 – PEUC e IPTU Progressivo
- Anexos II, III, IV e V

O sistema traduz legislação urbana em cálculos automáticos e visualização simplificada.

---

# 🧱 Stack Tecnológica

- Python
- Streamlit
- Supabase (Postgres)
- Folium (mapa)
- Shapely + STRtree (consulta espacial)
- PyProj (conversão de CRS)
- GeoJSON (zoneamento + ruas)

---

# 🗺️ Estrutura Geoespacial

## Arquivos locais:

- data/zoneamento_light.json
- data/ruas.json

## Funções principais:

- build_zone_index()
- find_zone_for_click()
- find_nearest_street()
- compute_location()

Sistema identifica:
- Zona (sigla + nome)
- Hierarquia viária
- Rua oficial

---

# 🗄️ Estrutura do Banco – Supabase

## 1️⃣ use_types
- code
- label
- category
- is_active

---

## 2️⃣ zone_rules
- zone_sigla
- use_type_code
- to_max
- tp_min
- ia_min
- ia_max
- to_sub_max
- recuo_frontal_m
- recuo_lateral_m
- recuo_fundos_m
- gabarito_m
- gabarito_pav
- area_min_lote_m2
- testada_min_meio_m
- testada_min_esquina_m
- allow_attach_one_side
- special_area_tag
- requires_subzone

---

## 3️⃣ parking_rules_v2 (Anexo IV)
- use_code
- base_metric
- rule_json
- general_notes
- source_ref

Sistema:
- Calcula vagas
- Aplica regra de arredondamento oficial
- Reduz 20% se VLT
- Dispensa não residencial ≤ 100m² via local

---

## 4️⃣ sanitary_profiles (Anexo III)
- sanitary_profile
- title
- rule_json
- source_ref

---

## 5️⃣ use_sanitary_profile
- use_type_code
- sanitary_profile

---

# ⚙️ Motor de Cálculo Urbanístico

Função principal:

compute_urbanism()

Calcula:

- Área do lote
- TO (Taxa de Ocupação)
- TP (Permeabilidade mínima)
- IA (Índice de Aproveitamento)
- Área máxima no térreo
- Área máxima total construída
- Envelope considerando recuos
- Pavimentos estimados por gabarito

---

# 🧠 Simulação "Para Leigo"

Função:

build_leigo_simulation()

Aplicável para:

- RES_UNI
- RES_MULTI

Permite:

- Inserir área construída desejada
- Inserir pavimentos desejados
- Ou usar máximos automáticos

Sistema verifica:

- TO respeitada?
- IA respeitado?
- Área permeável exigida?
- Resultado final: Viável ou Não Viável

Exibição simplificada com explicação didática.

---

# 🚗 Estacionamento

Prioridade:

1. parking_rules_v2 (Anexo IV oficial)
2. Fallback antigo (parking_rules)

Regra de arredondamento oficial:
Se décimo ≥ 5 → arredonda para cima.

Redução:
Até 20% se próximo ao VLT.

---

# 🚻 Sanitários

Baseado no Anexo III.

Função:

calc_sanitary()

Calcula:

- Lavatórios
- Aparelhos sanitários
- Mictórios
- Chuveiros

Com base em:

- Área útil informada

---

# 🔒 Regras Importantes de Segurança

- Multifamiliar não permite encostar lateral por padrão
- Encostar lateral só se zone_rules.allow_attach_one_side = true
- Sem regra cadastrada → sistema alerta

---

# 📊 Seções do App

1. Mapa interativo
2. Seleção de uso
3. Cálculo urbanístico
4. Viabilidade para leigo
5. Parâmetros detalhados da zona
6. Estacionamento
7. Sanitários
8. Debug (raw data)

---

# 🧩 Status Atual

✅ MVP funcional  
✅ Supabase integrado  
✅ Anexo III modelado  
✅ Anexo IV modelado  
✅ Simulação leiga funcionando  
✅ Debug completo  

---

# 🚀 Próximas Fases

Fase 2:
- Subzonas automáticas
- Áreas especiais (special_area_tag)

Fase 3:
- Interface mais profissional
- Exportar relatório PDF

Fase 4:
- API pública
- Versão SaaS

---

# 🛠️ Como Retomar o Projeto em Novo Chat

Copiar este arquivo inteiro
e dizer:

"Continuar desenvolvimento do Projeto Viabilidade MVP com base neste BACKUP_CONTEXT.md"

Isso restaura 100% do contexto técnico.
