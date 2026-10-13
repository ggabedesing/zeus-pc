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
- Única ação mutável atualmente presente: seleção de um plano de energia **já existente** no Windows, executada somente pelo Transaction Engine (preparar → consentimento único → executar → verificar por leitura independente → rollback automático se a verificação falhar).
- Nenhum executor genérico de PowerShell ou shell exposto pela API.

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
- recuperação de crash, posse durável de recurso e endurecimento de origem de requisição no Transaction Engine;
- PC Experience para aparência e personalização do Windows.

Veja também `docs/ARCHITECTURE.md` e `docs/ROADMAP.md`.
