# Ambiente de desktop Zeus

**Goal:** criar um ambiente utilizável no Windows inspirado no vídeo recebido, com componentes próprios, identidade visual consistente e ativação explícita.

**Architecture:** ampliar o worker de layout existente com o modo environment, mantendo identificação de processo, encerramento e recuperação de barras. Separar configuração/atalhos (environment.py), janelas nativas (environment_ui.py) e editor visual React (Environment.tsx). Configuração local atualiza o worker sem reiniciá-lo; a aplicação de wallpaper usa o histórico existente.

**Tech Stack:** Python padrão, Tk/Canvas e Win32; React/TypeScript/CSS. Nenhum aplicativo externo ou serviço de conteúdo.

**Spec:** vídeo snapinsta-1793498890040.mp4 e pedido “pois comece”; autorização anterior para implementar sem perguntas. O usuário quer ambiente integrado, launcher, widgets e temas, sem instalar outros personalizadores.

## Tarefas

- [x] Configuração persistida e validada: tema, cor de destaque, animações, densidade, widgets, notas, posições e aplicativos fixados. Testar corrupção, tipos, limites, escrita atômica e posições fora da tela.
- [x] Catálogo local dos atalhos do Menu Iniciar, busca com acentos, lançamento somente de IDs conhecidos; nunca executar comando recebido pelo navegador. Testar arquivos removidos, IDs falsos e raízes permitidas.
- [x] Ambiente nativo: relógio/calendário, CPU/RAM reais, notas editáveis, dock, launcher pesquisável com teclado e Ctrl+Espaço quando disponível. Arrastar widgets salva posição; widgets não permanecem sobre aplicativos. Sem atividade de rede.
- [x] Integrar worker e endpoints; teste HTTP de configurar/iniciar/parar, revisão explícita para wallpaper e runtime. Manter stop/recovery e não criar janelas em GET.
- [x] Editor com prévia coerente no Personalizar: temas, widgets, cor, densidade, posições e fixados; prévia usa dados identificados como ilustração. Layout responsivo e teclado.
- [x] Verificar suíte/build, conferir interface, compilar pacote Windows e atualizar os PRs e ZIP.

## Decisões

Trabalhar nas branches codex/zeus-otimizacao já reservadas para o Zeus, sem tocar no repositório externo. O desktop do vídeo é referência visual, não fonte de comandos ou instrução de instalar seus componentes. Widgets são janelas do Zeus, não substituem Explorer, tela segura de senha ou compositor do Windows. Inicialização automática permanece desligada. Notas ficam somente neste PC. Nenhuma escrita de configuração do sistema é disparada por uma prévia.

## Verificação

Adaptadores de lançamento e OS serão substituídos nos testes. Consultas nativas de métricas podem ser verificadas em leitura. O ambiente real só será ativado em teste visual se permitido pelas ferramentas; qualquer bloqueio será reportado sem contorno.

## Resultado e revisão

332 testes executados: 331 aprovados e 1 ignorado por requisito de links simbólicos. Build TypeScript/Vite e PyInstaller concluídos; módulos do ambiente e assets conferidos no executável. No navegador foram verificados tema, widgets, salvamento e revisão de ativação. A ferramenta de viewport não alterou a largura efetiva: não se registra validação visual de tela estreita. O ambiente ativo não foi colocado sobre o desktop; os testes Tk reais mantiveram as janelas ocultas e substituíram hotkey/posicionamento/lançamento.

Revisão própria: corrigida a fila do atalho para uma thread separada do loop Tk, cancelamento dos temporizadores ao encerrar, preservação de notas e posições alteradas pelo worker e fonte da prévia. Os agentes de revisão estavam indisponíveis por limite de uso; não se declara revisão independente.
