# VIABILIDADE SOBRAL – SYSTEM SPECIFICATION v1.1 (consolidado)

> **Objetivo deste arquivo**
>
> Este SPEC é o “contrato” do projeto. Em novas conversas, **cole este documento inteiro** para garantir continuidade técnica.  
> **Não** alterar arquitetura/fluxo/regras fora do que está aqui sem justificar.

---

## 0) Estado atual do projeto (o que já existe e funciona no monolito)

- Streamlit (layout wide) + Folium + streamlit-folium
- Detecção de **Zona** por clique no mapa usando `data/zoneamento_light.json` (Shapely + STRtree)
- Detecção de **Rua + Hierarquia** via `data/ruas.json` (nearest em EPSG:3857)
- Integração Supabase com secrets:
  - `SUPABASE_URL`
  - `SUPABASE_ANON_KEY`
- Consulta de regras urbanísticas em `zone_rules`
- Cálculo de TO/TP/IA e envelope por recuos (miolo) + limite real do térreo
- Flexibilização **Art. 112 (LC 90/2023)** para **Residencial Unifamiliar**
- Estacionamento:
  - v2 (`parking_rules_v2`) com JSON calculável
  - fallback antigo (`parking_rules`), quando não existir v2
- Sanitários:
  - `use_sanitary_profile` → `sanitary_profiles`
  - cálculo por faixas e fórmulas (“1/300 m²”)
- Bloco “para leigo” (residenciais) e **área útil** para base de sanitários/vagas
- Debug exibindo `location`, `rule`, `parking_v2`, `sanitary`, `simulação`

---

## 1) Requisitos inegociáveis (confirmados)

1. **Zona é obrigatoriamente detectada pelo mapa** (sem seleção manual).
2. **Supabase é a fonte única de regras urbanísticas** (sem hardcode por zona/uso).
3. Sem “fallback fixo” para recuos/índices. Se não houver regra no banco, o app deve avisar claramente.
4. Fluxo MVP:
   - Clique no mapa → detectar zona/via → selecionar uso → informar dimensões do lote → gerar estudo → mostrar parâmetros + cálculos + relatório.

---

## 2) Estrutura recomendada (arquitetura profissional – Opção 2)

> A reconstrução deve ser feita em branch `dev-architecture` e por camadas (mapa → supabase → motor → relatório).

### 2.1 Organização de pastas (proposta)

```
app.py
ui/
  map_view.py
  sidebar_inputs.py
  results_view.py
  report_view.py
domain/
  urban_calc.py
  art112.py
  parking_v2.py
  sanitary.py
  formats.py
infra/
  supabase_client.py
  repositories.py
data/
  zoneamento_light.json
  ruas.json
docs/
  VIABILIDADE_SOBRAL_SYSTEM_SPEC_v1.1.md
  BACKUP_CONTEXT.md
```

### 2.2 Responsabilidades

- **ui/**: apenas interface e coleta de inputs (sem regra de negócio).
- **domain/**: motor de cálculo (TO/TP/IA/recuos/art112/parking/sanitary).
- **infra/**: acesso ao Supabase (selects, mapeamentos).
- **app.py**: orquestra fluxo e session_state.

---

## 3) Dados geográficos (obrigatórios no repositório)

### 3.1 Arquivos
- `data/zoneamento_light.json`: polígonos de zonas, com propriedades contendo ao menos:
  - `sigla` (ex.: ZAM, ZAP…)
  - `zona` ou `nome` (nome amigável)
- `data/ruas.json`: linhas de vias com propriedades:
  - `log_ofic` (nome oficial)
  - `hierarquia` (ex.: local/coletora/arterial etc)

### 3.2 Funções base (esperadas)
- `find_zone_for_click(lat, lon) -> props_zone`
- `find_nearest_street(lat, lon, max_dist_m=120) -> props_rua`
- `compute_location(lat, lon) -> {zona_sigla, zona_nome, rua_nome, hierarquia, raw_zone, raw_rua}`

---

## 4) Supabase (fonte única de regras)

### 4.1 Secrets (Streamlit)
Obrigatórios (estes nomes não podem mudar):
- `SUPABASE_URL`
- `SUPABASE_ANON_KEY`

### 4.2 Tabelas e campos (mínimo para o MVP)

#### A) `use_types`
Usado para dropdown de usos.
- `code` (text) — ex.: `RES_UNI`, `RES_MULTI`, `HOS_HOTEL`
- `label` (text)
- `category` (text)
- `is_active` (bool)

#### B) `zone_rules`
Chave lógica: (`zone_sigla`, `use_type_code`)
Campos usados:
- **Índices**
  - `to_max` (float, 0–1)
  - `tp_min` (float, 0–1)
  - `ia_min` (float)
  - `ia_max` (float)
  - `to_sub_max` (float, 0–1) *(opcional)*
- **Recuos**
  - `recuo_frontal_m` (float)
  - `recuo_lateral_m` (float)
  - `recuo_fundos_m` (float)
  - `allow_attach_one_side` (bool) *(quando existir regra geral de encostar em 1 lateral)*
- **Altura**
  - `gabarito_m` (float) *(opcional)*
  - `gabarito_pav` (int) *(opcional)*
- **Lote**
  - `area_min_lote_m2`, `area_max_lote_m2` *(opcional)*
  - `testada_min_meio_m`, `testada_min_esquina_m`, `testada_max_m` *(opcional)*
- **Metadados**
  - `notes`, `observacoes`, `source_ref`

> Se não existir registro para (zona_sigla + use_type_code), o app deve exibir:
> “Sem regra cadastrada no Supabase para ZONA + USO.”

#### C) `parking_rules_v2` (Anexo IV – preferencial)
- `use_code` (text)
- `base_metric` (text) — ex.: `area_util_m2`, `apartamentos`, `leitos`, `lugares`, `unidades_hospedagem`
- `rule_json` (jsonb) — **JSON calculável** (ver modelo abaixo)
- `source_ref` (text) *(opcional)*
- `general_notes` (jsonb/text) *(opcional)*
- `notes` (text) *(opcional)*

#### D) `parking_rules` (fallback antigo)
- `use_type_code` (text)
- `metric` (text) — `fixed`, `per_unit`, `per_area`, `json_rule`
- `value` (float)
- `min_vagas` (int) *(opcional)*
- `rule_json` (jsonb) *(opcional)*
- `source_ref` (text) *(opcional)*

#### E) Sanitários (Anexo III)
- `use_sanitary_profile`:
  - `use_type_code` (text)
  - `sanitary_profile` (text)
  - `notes` (text) *(opcional)*
- `sanitary_profiles`:
  - `sanitary_profile` (text)
  - `title` (text)
  - `rule_json` (jsonb) — (ver modelo abaixo)
  - `source_ref` (text) *(opcional)*
  - `notes` (text) *(opcional)*

---

## 5) Motor urbanístico (domain) – regras e cálculos

### 5.1 Entradas mínimas do lote
- `testada_m`
- `profundidade_m`
- `esquina` (bool)
- se esquina: `corner_two_fronts` (bool)

Derivados:
- `area_lote = testada_m * profundidade_m`

### 5.2 TO / TP / IA (padrão)
- `area_max_ocupacao_to = to_max * area_lote`
- `area_min_permeavel = tp_min * area_lote`
- `area_max_total_construida = ia_max * area_lote`

### 5.3 Envelope por recuos (miolo)
- `largura_util = testada - recuos_laterais`
- `prof_util = profundidade - recuo_frontal - recuo_fundos`
- `area_miolo = largura_util * prof_util`
- `area_max_ocupacao_real = min(area_max_ocupacao_to, area_miolo)` quando ambos existirem.

### 5.4 Estimativa de pavimentos
- se `gabarito_pav` existir: usar
- senão se `gabarito_m` existir: `floor(gabarito_m / 3.0)` com mínimo 1
- senão: 1

### 5.5 Art. 112 (LC 90/2023) – Unifamiliar e Atrativas de Vizinhança

**Texto-chave:**
- Art. 112: para **uso residencial unifamiliar** e **atividades atrativas de vizinhança de pequeno porte**, aplica flexibilidade de recuos de **frente e laterais**, podendo zerar, desde que cumpra **TP mínima** e **TO máxima**.
- Art. 96: atrativas de vizinhança **até 250 m²** podem zerar frente/laterais com as mesmas condições; se implantar no alinhamento, manter permeabilidade visual (fachada ativa).

**Implementação esperada (para Unifamiliar):**
- Opção 1: **Recuos padrão** (usar recuos do `zone_rules`)
- Opção 2: **Art. 112** (zerar `recuo_frontal_m` e `recuo_lateral_m`; manter `recuo_fundos_m`)
- Ambas respeitam TO/TP/IA.

---

## 6) Estacionamento v2 (Anexo IV) – JSON padrão (mínimo)

### 6.1 Modelo `rule_json` (exemplo)
```json
{
  "use_code": "COM_EXEMPLO",
  "base_metric": "area_util_m2",
  "rules": [
    {"type": "ratio", "per_m2": 30, "text": "1 vaga a cada 30 m²"},
    {"type": "fixed", "value": 1, "text": "mínimo 1 vaga"}
  ],
  "cargo_loading": {"text": "Quando aplicável, prever área de carga/descarga."},
  "general_notes": ["Regras conforme Anexo IV."]
}
```

### 6.2 Arredondamento (Anexo IV)
- Resultado com 1 casa decimal; se décimo >= 0,5 arredonda para cima.

### 6.3 Ajustes de política do MVP
- Checkbox “perto do VLT”: reduz 20% e aplica `ceil`.
- Checkbox “via local”: dispensa **não residencial** até 100 m² quando `base_metric == area_util_m2`.

### 6.4 Regra de negócio confirmada
- **Residencial Unifamiliar:** **não exige vaga mínima** (Anexo IV). No resultado, exibir “Não exigido”.

---

## 7) Sanitários (Anexo III) – JSON padrão (mínimo)

### 7.1 Modelo `rule_json` (exemplo)
```json
{
  "groups": [
    {
      "group": "GERAL",
      "bands": [
        {
          "min_m2": 0,
          "max_m2": 300,
          "lavatórios_formula": "1/300m² ou fração",
          "aparelhos_sanitários": 1,
          "mictórios": 0,
          "chuveiros": 0
        }
      ]
    }
  ]
}
```

### 7.2 Regras
- Seleciona faixa (`band`) pela área útil.
- Campos podem vir como número fixo ou fórmula “1/XXX m²” → `ceil(area / XXX)`.

---

## 8) UX – Unifamiliar (requisitos mínimos para leigo)

### 8.1 Inputs essenciais (sidebar)
- Testada (m)
- Profundidade (m)
- Esquina? (e “2 frentes?” se sim)
- Tipo da casa: **Térreo / Duplex / Triplex / Outro**
- Área pretendida no térreo (m²) *(opcional)*
- Área construída total (m²) *(opcional; se vazio usar máximo pelo IA)*

### 8.2 Regras de exibição
- Se usuário **não informar área**:
  - mostrar máximos permitidos (TO/TP/IA)
  - mostrar **Opção 1 (recuos)** e **Opção 2 (Art.112)** com valores máximos
- Se usuário informar área pretendida:
  - calcular “TO do projeto” e comparar com limites
  - exibir “você precisa deixar pelo menos X m² permeável” (TP)
- Estacionamento: “não exigido” para unifamiliar

---

## 9) Relatório (modelo consolidado)

Formato:
- Pergunta → Resposta
- Explicação didática
- Cálculo demonstrado
- Base legal implícita
- Quadro técnico (Anexo II) ao final

Conteúdo mínimo:
1) Quanto posso ocupar no chão? (TO + recuos padrão + Art.112)  
2) Quanto preciso deixar permeável? (TP + tabela de pisos do Art.108)  
3) Quanto posso construir no total? (IA + gabarito)  
4) Estacionamento (Anexo IV: unifamiliar sem exigência)  
5) Quadro técnico Anexo II + observações aplicáveis

---

## 10) Versionamento e implantação (para nunca mais “perder versão”)

- Produção: branch `main`
- Desenvolvimento: branch `dev-architecture`
- Criar arquivo `VERSION.txt` na raiz, ex.:
  - `viabilidade-sobral dev-architecture v1.1`
- A cada mudança grande: atualizar `VERSION.txt` e mencionar no commit.

---

## 11) Checklist de teste rápido (Unifamiliar)

- Mapa aparece e permite clique
- Pin aparece e zona/via são detectadas
- “Gerar estudo” busca `zone_rules`
- TO/TP/IA em m² aparecem
- Opção 1 (recuos) e Opção 2 (Art.112) aparecem
- Ao informar área pretendida, recalcula TO do projeto
- Estacionamento: “Não exigido”
- Relatório aparece e permite download (.md)
- Debug mostra objetos esperados
