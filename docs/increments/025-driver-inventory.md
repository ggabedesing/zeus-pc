# Increment 025 — Inventário de drivers estilo Driver Booster (somente leitura)

Data: 2026-10-21

## Objetivo

Dar ao Zeus o coração do Driver Booster **sem executar nada**: ver, no app,
cada driver do PC com provedor, classe, versão, data e — o mais importante —
quais estão **sem assinatura**, com **assinatura desconhecida** ou **antigos**.
Todo o fluxo é leitura; nenhuma instalação, download ou rollback de driver
existe neste incremento (eles vêm nos próximos, sempre pelo Transaction
Engine com consentimento).

## Duas fontes complementares (sem privilégios especiais)

- **Loja de drivers** — `pnputil /enum-drivers`: pacotes `oem*.inf` com
  provedor, classe, GUID, versão, data e assinante. Nesta máquina pt-BR o
  pnputil escreve em **ANSI (cp1252)** — o mesmo byte `0xE3` é "ã" lá e
  "O com grave" em cp850 (OEM), e **ambos decodificam sem erro**: a ordem
  de tentativas do `_decode_output` (usado pelo powercfg) escolhia errado.
  O decode do pnputil é agora escolhido **por evidência** —
  `_decode_for_labels` pontua cada candidato (UTF-8, OEM, ANSI, latin-1)
  pelos rótulos conhecidos presentes no texto. Rótulos aceitos em **pt-BR
  e en-US** (`Nome publicado:` / `Published Name:`, incluindo os reais
  `Nome do Signatário:` e `GUID de Classe:`), campos desconhecidos de
  builds novas do Windows são ignorados sem quebrar o bloco. O pt-BR ainda
  embute a data na própria linha da versão (`Versão do Driver:
  07/18/1968 10.1.14.7`, data em formato americano) — separada em
  `version` + `date` no parsing.
- **Dispositivos** — `Win32_PnPSignedDriver`: cada dispositivo com driver
  aplicado, versão, data (`/Date(ms)/`, ISO, DMTF ou epoch puro) e
  `IsSigned` — que pode ser `True`, `False` ou **ausente**.

## Classificação honesta (sem catálogo online)

| Status | Significado |
|---|---|
| `UNSIGNED` | `IsSigned` é `False` de verdade |
| `UNKNOWN_SIGN` | `IsSigned` ausente — o Windows não afirmou nada |
| `OLD` | assinado e com mais de `OLD_DRIVER_DAYS` (1095 dias ≈ 3 anos) |
| `OK` | assinado e dentro da janela de idade |

`UNKNOWN_SIGN` **não** é contado como "sem assinatura" — separar o que o
Windows negou do que ele não sabe é a diferença entre diagnóstico honesto
e assustador. "Antigo" usa critério de data explícito; dizer "desatualizado"
exigiria catálogo de fabricante (próximo incremento: discovery de updates).

**Datas-placeholder não são idade.** O Windows reporta datas que não são a
data real do driver e tratá-las como "antigo" geraria falsos positivos em
escala (nesta máquina: de 127 para 24). Estão em `NULL_DRIVER_DATES` e
viram `date = None` (a UI mostra sem "desde") e saem do teste de idade:

- `2006-06-21` — baseline dos drivers inbox do Windows (102 dispositivos
  aqui, todos ACPI/Microsoft sem data real no INF);
- `1968-06-21` / `1968-07-18` — datas-nula do WDK (`07/18/1968`
  observado no INF da Intel na loja);
- `1970-01-01` — `DriverDate=0` (epoch Unix) observado no WMI no device
  Intel Gaussian Mixture Model;
- `1601-01-01` — epoch do FILETIME.

Ordenação dos dispositivos: `UNSIGNED` → `UNKNOWN_SIGN` → `OLD` → `OK`, e
alfabética dentro de cada grupo — os problemas aparecem primeiro.

## API

`GET /api/drivers` (loopback + guards do increment 020, `Cache-Control:
no-store`) devolve:

- `store[]` / `devices[]` — listas normalizadas;
- `summary` — `device_count`, `unsigned_count`, `unknown_sign_count`,
  `old_count`, `store_count`, `store_unsigned_count`, `checked_at`;
- `old_after_days` — o limiar usado, para a UI não inventar número;
- `store_error` / `device_error` / `message` — melhor-esforço: uma fonte
  falhando não derruba a outra (e o motivo aparece na tela).

Cada resposta registra `drivers_inventoried` no log estruturado com as
contagens.

## UI

- Novo tile **Drivers** na página Sistema (tile-row virou `auto-fit` para
  acomodar 5 tiles sem quebrar no mobile);
- painel com resumo (instalados / sem assinatura / desconhecida / antigos /
  pacotes na loja com contagem de sem assinante), botão **"Só com problema"**
  que alterna o filtro e volta a renderizar, lista limitada a 60 linhas com
  "+N outros", e a marcação **"somente leitura"** sempre visível;
- tags novas: `tag-driver-unsigned` (perigo), `tag-driver-unknown_sign` e
  `tag-driver-old` (atenção), `tag-driver-ok`;
- data exibida como data real (`formatDate`), provedor/classe/versão numa
  linha secundária.

## Testes (27 novos — 231 no total)

`tests/test_drivers.py`:

- parser `pnputil` pt-BR (2 blocos, um sem assinante) e en-US (1 bloco);
- rótulo desconhecido no meio do bloco não quebra o parsing;
- entrada vazia/sem rótulo → `[]`;
- datas: `/Date(ms)/`, ISO, DMTF (com offset `+000`, formato WMI real),
  epoch puro 13 dígitos; entradas inválidas → `None`;
- classificação: `False`→UNSIGNED, `None`→UNKNOWN_SIGN, além do limiar→OLD,
  exatamente no limiar→OK, sem data→OK;
- `normalize_device`: campos e status; `IsSigned` ausente vira `None`;
- rota `GET /api/drivers` num Handler real com coletores interpolados
  (status 200, `no-store`, contagens, `unsigned` na loja) e o mesmo guard
  de Host do resto da API (403 `evil.example.com`);
- **reparo do incremento** — formato real pt-BR desta máquina: rótulos
  acentuados + `Nome do Signatário` + `GUID de Classe` + versão com data
  embutida, e o split versão/data no `_annotate_store`;
- decode por evidência (`_decode_for_labels`): cp1252 e cp850 da mesma
  string ambos resolvem para os rótulos corretos; UTF-8 preferido quando
  válido; entrada vazia; pares reais de cada máquina (OEM+ANSI) e pares
  pt-BR/en-US simulados por mock dos codepages — determinísticos em
  qualquer CI;
- datas-placeholder: `1970-01-01`/`1968-07-18`/`1601-01-01` não viram OLD,
  `1968-07-18` embutido na versão vira `date = None`, `WINDOWS_DRIVER_EPOCH
  = 2006-06-21` fica em `NULL_DRIVER_DATES` e `DriverDate=/Date(0)/` no
  `normalize_device` vira `date = None` com status `OK`.

Falhas encontradas e corrigidas **na causa** durante o incremento:

1. a regex DMTF não aceitava o sufixo de offset `+000` do formato real do
   WMI;
2. o decode OEM-first do pnputil produzia `versoo do driver` (0xE3 em
   cp850) — nenhum rótulo de versão/assinante casava: `version`, `signer` e
   `class_guid` vieram nulos e os 46 pacotes pareciam todos sem assinante;
3. rótulos reais ausentes do mapa (`Nome do Signatário`, `GUID de Classe`);
4. a data embutida na linha da versão contamina o campo `version`;
5. a baseline 2006-06-21 (e as demais datas-placeholder) inflavam
   `old_count` para 127 alarmes falsos;
6. no CI (run 49): o teste de decode codificava em **cp850 fixo** —
   o OEM desta máquina pt-BR —, mas o runner en-US usa OEM **cp437**;
   o contrato certo é decodificar os codepages reais da máquina, então
   o teste agora percorre OEM+ANSI reais e cobre os pares pt-BR/en-US
   com codepages simulados.

## Verificação

- 231 testes locais OK; `compileall` OK;
- servidor real em `http://127.0.0.1:8000` → tile Drivers → painel com
  dados reais desta máquina: **161 dispositivos, 0 sem assinatura,
  2 assinatura desconhecida, 24 antigos, 46 pacotes na loja (0 sem
  assinante)**, filtro "Só com problema" listando 26 de 26, datas-nula
  sem "desde", console do navegador vazio;
- `GET /api/drivers` via curl: JSON com `store`/`devices`/`summary` —
  contagens conferidas contra a coleta direta (`store_error`/
  `devices_error` = `None`).

## Fora do escopo (próximos incrementos)

- Discovery de updates (catálogo de fabricante → "desatualizado" de verdade);
- Download oficial + hash/assinatura;
- Instalação/rollback de driver pelo Transaction Engine com consentimento.
