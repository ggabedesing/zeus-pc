# Increment 022: Painel desktop legível + decodificação honesta do PowerShell

## Objetivo

Responder ao feedback real de quem usa o app: a interface mostrava
**JSON cru** (`JSON.stringify` em `<pre>`) ao explorar "Meu PC" e
"Mais", virava **barra inferior de celular** em janela estreita, e não
dizia **"instalei, e agora?"** — nada indicava o próximo passo.

Do lado do dado, o Windows entregava nomes corrompidos:
`Desempenho Máximo` chegava como `Desempenho M\xa0ximo` — o
`powercfg` escreve na OEM da consola (cp850: `á` = 0xA0) e o Python
lia como ANSI (cp1252: 0xA0 = espaço inquebrável).

## O que mudou na interface (`web/`)

- **Layout desktop primeiro**: sidebar fixa 216px com as 5 telas
  (Painel, Meu PC, Desempenho, Otimizar, Sistema); a barra inferior de
  celular só aparece abaixo de 760px e ainda assim com as labels.
  Tipografia densa (14px), raios de 10px, sem gradiente/animção
  decorativa — visual de ferramenta de sistema, não de app de brinquedo.
- **Zero JSON no fluxo normal**: nenhuma tela imprime `JSON.stringify`;
  todo domínio vira cards com pares `rótulo → valor` legíveis
  (`METRIC_LABELS` em pt-BR, datas `/Date(...)/` formatadas, bytes em
  GB, booleanos em Sim/Não, listas resumidas com `+N outros`). O JSON
  bruto continua disponível **só** no drawer "Modo técnico" (opt-in).
- **"Instalei, e agora?"**: a tela inicial abre já com a **última
  análise do histórico** (veredito, contagem, resumo da máquina em 6
  chips, recomendações), botão único de ação e cartão do **Cérebro do
  Zeus** (estado da IA local vindo do doctor). Ao vivo: CPU/GPU/RAM/
  temperatura com polling de 3s.
- **"Meu PC" organizado**: 30 domínios agrupados em 6 seções
  (Sistema, Hardware, Segurança, Rede, Software e inicialização,
  Serviços e processos) em vez de lista plana de `<details>` com JSON.
- **"Otimizar" explica**: cada finding mostra motivo, por que está
  impedido (bloqueios traduzidos para português), benefício, risco,
  confiança e se pode ser aplicado com consentimento.
- **Página "Sistema" legível**: tiles de Atualizações/Inicialização/
  Segurança/Energia renderizam linhas `rótulo → valor` a partir da
  API; o JSON sumiu do fluxo padrão.

## O que mudou no backend (decodificação)

- `powershell.py`: `run()` captura **bytes** e `_decode_output()`
  tenta UTF-8 → OEM da consola (`GetOEMCP`) → ANSI → latin-1, com
  remoção de BOM. `text=True` (que usava o ANSI errado) saiu.
- `actions.py` (`list_power_plans`, `set_power_plan`) e `probes.py`
  (`power_probe`) faziam `subprocess.run(..., text=True)` direto —
  o mesmo bug em mais três pontos — e agora passam por
  `_decode_output`.
- Motivo: o bug não é cosmético, ele entregava dado **errado** à API
  e à UI (nomes de plano com caractere errado podiam, em tese,
  interferir em comparações de texto).

## Verificações (executadas após todos os edits)

- `unittest discover` → **187 testes OK (exit 0)**; `compileall` OK.
  6 novos em `tests/test_powershell_decoding.py`: cp850 acentuado
  recuperado (patch da OEM, determinístico independente do idioma da
  máquina), UTF-8 com e sem BOM, fallback ANSI quando sem OEM, vazio.
- **Prova real**: `GET /api/power-plans` no servidor reiniciado →
  assert `n == "Desempenho Máximo"` passa (codepoint `0xe1`); antes
  do fix o mesmo payload tinha `0xa0`.
- **Navegador (DOM real, 5 telas)**: `pre` = 0 em todas; `rawJson` =
  false; Painel com 6 chips + 1 recomendação + estado do cérebro;
  Meu PC com 30 cards em 6 grupos; Otimizar com plano e contagens
  `1/0/0/0`; Desempenho com 6 processos reais (VALORANT 18,58% CPU);
  painel Energia da página Sistema com "Desempenho **Máximo**" acentuado.
- **Console**: limpo após reload; 9× `/api/observer/sample → 200`,
  nenhum erro (os `ERR_CONNECTION_REFUSED` anteriores eram da janela
  em que o servidor foi reiniciado).

## Pendência (ação do usuário)

Abrir `http://127.0.0.1:8000` no navegador e avaliar o novo visual —
o screenshot automatizado do webview não funciona neste ambiente, então
a revisão visual final é humana. O GGUF/llama-server segue pendente
como no Increment 021.
