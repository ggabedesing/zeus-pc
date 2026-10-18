# Zeus PC

O **Zeus PC** é uma central local para Windows focada em diagnóstico, otimização contextual, monitoramento e personalização.

Esta versão foi recriada do zero no GitHub para eliminar a bagunça estrutural do projeto antigo.

## Estado atual

- Interface local premium e simples.
- Diagnóstico de CPU, GPU, RAM, armazenamento, placa-mãe, BIOS, Windows, energia, segurança, inicialização e Windows Update.
- Observer leve para CPU, GPU, RAM e temperatura da GPU quando a fonte estiver disponível.
- Motor inicial de recomendações baseado em evidências.
- Modo técnico separado da experiência normal.
- Servidor ligado somente em `127.0.0.1`.
- Única ação mutável atualmente presente: seleção de um plano de energia **já existente** no Windows, executada somente pelo Transaction Engine (prepare → consentimento único → APPLY → VERIFY independente → rollback automático se o VERIFY falhar).
- Lock por recurso para impedir duas mutações simultâneas no mesmo recurso.
- Nenhum executor genérico de PowerShell ou shell exposto pela API.
- Fundação do agente local: contexto determinístico orçado em tokens (`/api/agent/context`) e planner de schema fechado (`/api/agent/plan`) — sem nuvem, sem chaves de API, sem terceiros.
- **Cérebro local (Model Hub)**: registro fechado de 7 modelos GGUF (japoneses + multilingue), runtime somente-loopback para `llama-server`, roteador determinístico por tarefa (`GET /api/brain/models`, `GET /api/brain/status`, `POST /api/brain/invoke`) e relatório de saúde `python -m zeus_pc.doctor` — tudo offline.
- **Gerenciador de modelos**: download/import de GGUF com **aceite de licença registrado** e **hash SHA-256 verificado** (só HTTPS de hosts allowlistados), verificação de tampering (`POST /api/brain/verify`), LRU de disco sob guarda de RAM com preview e confirmação (`GET /api/brain/storage`, `POST /api/brain/prune`) e **benchmark fechado JA/EN/PT** cujas notas alimentam o roteador (medido > declarado).
- **API local endurecida**: `Host` somente loopback (anti DNS rebinding), `Origin` somente same-origin nas mutações (anti CSRF de navegador) e corpo de `POST` obrigatoriamente `application/json`; rejeições auditadas no log. SQLite em **WAL** com `busy_timeout` para leitores e escritores concorrentes.

## Como instalar no PC

No PowerShell:

```powershell
cd C:\Users\ggabe\Desktop
git clone https://github.com/ggabedesing/zeus-pc.git zeus-novo
cd zeus-novo
.\start.bat
```

Depois abra:

`http://127.0.0.1:8000`

Para provar o ciclo completo no PC (modelo → servidor → invoke medido →
trocar e restaurar plano de energia com consentimento real):

```powershell
python -m zeus_pc.skeleton --model-id LLM_JP_1_8B `
  --download-url <url-do-gguf> --accept-license
```

O relatório JSON diz o que passou, o que falhou e o que foi pulado;
exit code 1 somente quando algo falhou.

> O repositório é privado. O Git no seu PC precisa estar autenticado no GitHub.

## Estrutura

```text
src/zeus_pc/
  models.py       contratos e estados
  powershell.py   leitura controlada via PowerShell
  probes.py       inventário e diagnóstico read-only
  engine.py       recomendações
  observer.py     telemetria leve
  actions.py      ações semânticas allowlisted
  transaction.py  prepare, consentimento, execução e rollback
  agent/
    context.py    digest de contexto orçado em tokens
    planner.py    plano de schema fechado + risco/consentimento
    tools.py      catálogo ToolSpec com JSON Schema (/api/agent/tools)
  brain/
    registry.py   contrato fechado dos modelos GGUF (ModelSpec)
    runtime.py    llama-server em loopback, RAM e probe /health
    router.py     roteamento determinístico por tarefa (medido > declarado)
    serve.py      sobe o llama-server local (argv puro, sem shell)
    manager.py    install com hash + licença, verify e LRU sob guarda de RAM
    benchmark.py  suite fechada JA/EN/PT (fidelidade + velocidade)
  doctor.py       relatório de saúde (python -m zeus_pc.doctor)
  skeleton.py     walking skeleton: modelo → serve → invoke → trocar e restaurar plano
  server.py       API local

web/
  index.html
  styles.css
  app.js

tests/
  test_engine.py
  test_actions.py
```

## Filosofia

O fluxo alvo é:

```text
OBSERVAR
→ INVENTARIAR
→ DIAGNOSTICAR
→ INTERPRETAR
→ DECIDIR
→ PROTEGER
→ EXECUTAR
→ VALIDAR
→ REGISTRAR
→ REVERTER
```

O Zeus não deve executar “tweaks universais”. Uma recomendação precisa considerar o estado real da máquina, perfil do usuário, compatibilidade, risco, benefício esperado e possibilidade de verificação.

## Segurança

O projeto não deve desativar Defender, Firewall, Secure Boot, TPM ou outras proteções apenas para tentar ganhar desempenho.

Drivers e atualizações devem usar fontes oficiais.

BIOS/firmware não devem ser atualizados automaticamente enquanto não existir estratégia específica e verificável do fabricante.

## Próximas áreas

O roadmap inclui:

- inventário completo de drivers e dispositivos;
- rede e latência;
- serviços e tarefas agendadas;
- Windows integrity / SFC / DISM em modo diagnóstico;
- central de drivers e atualizações;
- análise de gaming e streaming;
- baseline e comparação antes/depois;
- limpeza segura com preview;
- startup controlado;
- Crash recovery para transações interrompidas;
- Novas famílias de ação (startup, limpeza segura) com preview;
- PC Experience para aparência e personalização do Windows.

Veja também `docs/ARCHITECTURE.md` e `docs/ROADMAP.md`.
