# Increment 021: Walking skeleton runner (`python -m zeus_pc.skeleton`)

## Objetivo

Transformar o próximo passo manual em **um comando só** para o PC Windows
real: baixar/verificar o modelo, subir o servidor, fazer um invoke
medido e executar **duas transações reais com consentimento** (trocar o
plano de energia e restaurar o original), terminando com um relatório
JSON que diz exatamente o que passou, o que falhou e o que foi pulado.

Este incremento entrega o **runner**; a execução de verdade com um GGUF
de GB acontece no seu PC (seção "Como rodar").

## Como rodar no Windows

```powershell
cd <repo>
# opção A: modelo que você já importou/baixou via POST /api/brain/install
python -m zeus_pc.skeleton

# opção B: baixar direto (aceite de licença explícito + hash gravado)
python -m zeus_pc.skeleton --model-id LLM_JP_1_8B `
  --download-url https://huggingface.co/.../resolve/main/....gguf `
  --accept-license
```

Pré-requisitos: `llama-server` no PATH (ou `ZEUS_LLAMA_SERVER`) e o
modelo com GGUF presente. Saída: JSON com `ok`, `counts` e uma linha
por etapa; **exit code 1 somente quando há `falha`**.

## Cadeia de etapas

| etapa | prova | depende de |
|---|---|---|
| `doctor` | banco/powercfg/llama-server/GGUFs/RAM | — |
| `model` | instalação com licença + **SHA-256 conferido** (`verify`) | — |
| `serve` | subprocesso `python -m zeus_pc.brain.serve` (argv puro) responde `/health` | model ok |
| `invoke` | um turn real medido (latência + tokens) | serve ok |
| `transaction` | prepare → consentimento → execute **troca** + prepare → consentimento → execute **restauração** | — |

Regras honestas do relatório:

- dependente de etapa que não ficou `ok` → `pulado` com o motivo, nunca
  sucesso fake;
- exceções viram `falha` (`_safe_step`), o runner nunca estoura traceback;
- **restauração falhou → `FAILA CRITICA`** no detalhe, com o nome do
  plano original — é o pior cenário e precisa gritar;
- o subprocesso do servidor é sempre terminado (finally), e o log dele é
  anexado ao detalhe quando o serve falha;
- nenhuma mutação contorna o Transaction Engine: os tokens de
  consentimento são reais e cada transação fica no journal.

## Verificações

- `unittest discover` → **181 testes OK** (13 novos em
  `tests/test_skeleton.py`); `compileall` OK;
- ciclo de transação testado de verdade em DB temporário com ação
  tipo-plano: troca + restauração = 2 transações `VERIFIED`, estado
  restaurado, chamadas exatas `[GUID_B, GUID_A]`;
- caminhos de falha cobertos: timeout do serve (processo terminado),
  saída precoce do servidor com cauda de log, invoke indisponível,
  `llama-server` ausente, troca reprovada (restauração não tentada),
  restauração reprovada (`FAILA CRITICA`), plano desconhecido,
  banco quebrado reportado sem crash;
- CLI real neste ambiente: execução padrão → 4 `pulado`, exit 0;
  `--model-id NAO_EXISTE` → `falha`, exit 1.

## Pendência (ação do usuário)

Rodar no PC Windows com um GGUF real — é a prova que nenhum sandbox
faz: download de GB, RAM de verdade, `powercfg` real e o restore
confirmado pelo Windows.
