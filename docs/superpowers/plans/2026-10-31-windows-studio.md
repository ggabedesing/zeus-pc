# Zeus Windows Studio Implementation Plan

**Goal:** manter a interface simples e ampliar o motor com ações reais de Windows, GPU, manutenção e personalização.

**Architecture:** reaproveitar o diário e a restauração existentes. Novos adaptadores fechados de preferências, GPU e personalização; manutenção demorada em processos separados, com progresso e elevação solicitada somente quando a pessoa inicia a operação. O navegador local apresenta catálogos e estados reais.

**Tech Stack:** Python padrão/Win32/PowerShell, React/TypeScript, PyInstaller.

**Spec:** pedido do usuário nesta conversa: profundidade de otimização, interface acessível e personalização do próprio Windows, com software gratuito.

## Entregas

- [x] Preferências reais de jogos, gravação em segundo plano, Explorer e barra de tarefas, com captura de valores ausentes/existentes e restauração exata.
- [x] Inventário de GPU e escolha de preferência gráfica para aplicativos em execução ou executáveis adicionados manualmente; preservar outros campos de UserGpuPreferences e informar que é necessário reabrir o aplicativo.
- [x] Temas, papéis de parede próprios/importados e perfis nativos de animações aplicados ao Windows, com restauração.
- [x] Manutenção de armazenamento, DISM/SFC e diagnóstico de rede em trabalhos com saída real; elevar somente operações que exigem administrador e nunca executá-las durante os testes desta sessão.
- [x] Renderizador animado e dock/sidebar próprios do Zeus, sem instalar aplicativos de terceiros. Ativação explícita, pausa contextual do papel animado e restauração da barra nativa quando aplicável.
- [x] Interface com Otimizar, Personalizar, Sistema e Histórico. Exibir resultados de execução, reinicialização/elevação necessárias e falhas reais.
- [x] Testes de adaptadores e fluxo HTTP com dados descartáveis; build frontend, revisão, pacote Windows atualizado e alterações nos PRs existentes.

## Decisões de implementação

A autorização anterior para agir sem perguntas continua válida. Não há uma promessa de alterar qualquer parte do Windows por qualquer método. O formulário seguro de senha não será substituído por uma simulação. Preferência de GPU gravada e reparo de integridade não serão anunciados como FPS medido. Temas de aparência não serão tratados como otimizações de velocidade universais.

## Verificação

Testar ausência/valor/tipo no registro, restauração, preservação de campos gráficos desconhecidos, argumentos rejeitados, progresso/falha de trabalhos e importação de imagens. Nenhum teste aplica mudanças ao Windows real ou instala complementos.

Resultado da verificação: 313 testes executados, 312 aprovados e um ignorado por requisito de links simbólicos do ambiente; build TypeScript/Vite e compilação PyInstaller concluídos. Recursos nativos mutáveis não foram ativados no Windows desta sessão.
