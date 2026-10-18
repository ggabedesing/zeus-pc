# Increment 019: Model Manager (install + hash + licença, LRU sob guarda de RAM, benchmark JA/EN/PT)

## Objetivo

Transformar "7 modelos declarados" (018) em "modelos instalados, verificáveis e medidos":

- instalação com **aceite explícito de licença** registrado em SQLite e **hash SHA-256** verificado;
- **LRU sob guarda de RAM**: ordem de permanência em disco determinística, com demissão de modelos que não cabem na memória, aplicada só com confirmação explícita;
- **benchmark fechado JA/EN/PT** cujas notas alimentam o roteador (medido > declarado).

Nenhuma mutação de máquina acontece aqui: apagar um GGUF é apagar um arquivo que o próprio Zeus instalou e rastreia.

## Decisão de segurança

- **Licença é porta obrigatória**: `install` sem `accept_license=true` falha com o nome exato da licença. O aceite fica em `model_installs.license_accepted_at` (SQLite).
- **Download fail-closed**: somente `https://` e somente hosts da allowlist (host do `source_url` do spec + CDNs conhecidos do HuggingFace/GitHub). Qualquer outro host é negado antes de qualquer I/O.
- **Hash antes de confiar**: o download é streamado para `.part`, tem SHA-256 calculado no caminho e só é renomeado para o `filename` depois de confere com o `expected_sha256`. Mismatch → nada sobra (nem arquivo, nem registro).
- **Import nunca toca o arquivo do usuário**: copia, confere o hash e mantém o original intacto.
- **Só arquivos rastreados são apagados**: `prune`/`remove` ignoram qualquer GGUF solto em `models_dir` que o Zeus não instalou, e exigem `confirm=true`.

## SQLite v4

`SCHEMA_VERSION` 3 → 4, migração não destrutiva:

- `model_installs`: model_id, filename, license_name, license_accepted_at, sha256, size_bytes, source (download/import), source_url, installed_at, last_used_at;
- `model_benchmarks`: id, model_id, language, task, created_at, elapsed_ms, prompt_tokens, completion_tokens, tokens_per_s, fidelity, score.

`benchmark_scores()` devolve a média da **linha mais recente por (modelo, idioma)** — determinístico dado o conteúdo do banco.

## LRU sob guarda de RAM

Ordem de permanência (estável, sem aleatoriedade):

1. modelos que **cabe na RAM disponível** primeiro (demissão por `memoria`);
2. entre esses, os **mais usados primeiro** (`last_used_at`, tocado a cada invoke com sucesso; nunca usado = mais antigo);
3. desempate por `model_id`.

Tudo que passa do `budget_gb` (padrão 30 GB, `ZEUS_MODELS_BUDGET_GB`) vira candidato a evicção com motivo exato. `GET /api/brain/storage` devolve o plano; `POST /api/brain/prune` só apaga com `confirm=true`.

## Benchmark fechado

- 6 prompts fixos (2 por idioma: ja/en/pt), tarefas CHAT/SUMMARIZE/CLASSIFY — nada é gerado em runtime;
- **fidelidade de idioma** (60%): a resposta volta mesmo no idioma pedido? (kana/kanji p/ JA, stopwords/diacríticos p/ PT, stopwords p/ EN);
- **velocidade** (40%): tokens/s saturando na referência de CPU (20 tok/s) — acima disso não soma nota;
- cada turn é journalizado; o agregado por modelo vira empate no roteador **acima** do `quality_rank` estático (medido > declarado), com o motivo exato no `reason`.

## API

- `POST /api/brain/install` — `{model_id, accept_license, url|source_path, expected_sha256?}` → 201 com o registro; 409 para licença/host/hash/arquivo existente;
- `POST /api/brain/verify` — `{model_id}` → `ok` / `alterado` (tamper) / `ausente` / `nao_rastreado`;
- `GET /api/brain/storage?budget_gb=` — installs rastreados, uso, plano LRU e notas;
- `POST /api/brain/prune` — `{budget_gb?, confirm}` → removed/kept/freed_gb;
- `POST /api/brain/benchmark` — `{model_id, languages?}` → notas por idioma + agregado (409 se o runtime estiver parado);
- `POST /api/brain/invoke` agora **toca o LRU** e roteia usando as notas medidas quando existem.

`GET /api/brain/status` ganhou os checks `model_storage` (rastreados vs. arquivo presente) e `brain_benchmarks`, mais os blocos `storage` e `benchmark_scores`.

## Verificações

- `unittest discover` → **153 testes OK** (33 novos em `tests/test_model_manager.py`); `compileall` OK;
- dois testes antigos derrubados pela nova versão do schema corrigidos na intenção (`assertGreaterEqual(SCHEMA_VERSION, 3)`, a migração v2→v3 continua coberta);
- smoke HTTP real: storage vazio, licença negada 409, URL http negada 409, import 201 com hash conferindo, verify `ok`, invoke/benchmark 409 com runtime parado, prune sem confirmação 409;
- ponta a ponta com runtime stub loopback: benchmark 6/6 fidelidade 1.0 → nota 100, invoke roteou por "melhor nota de benchmark 100.0", `last_used_at` tocado, doctor com os 6 checks.

## Limitações

- nenhum GGUF real (GB) foi baixado aqui: o caminho de download HTTPS + allowlist foi exercitado com transporte injetado (o guard roda antes do I/O em qualquer caso); a primeira descarga real acontece no PC Windows;
- velocidade absoluta do benchmark depende da máquina — a nota comparativa entre modelos na mesma máquina é o que alimenta o roteador.

## Próximo

Increment 020 — loop do agente consumindo contexto + cérebro, com os modelos instalados e medidos pelo 019.
