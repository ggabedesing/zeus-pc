# Windows Studio do Zeus

O Zeus mantém quatro entradas: Otimizar, Personalizar, Sistema e Histórico. Todos os módulos de personalização são código do próprio Zeus. O pacote não instala nem controla Lively, TranslucentTB ou aplicativos equivalentes.

## Recursos implementados

- Quatro temas com papel de parede gerado localmente, claro/escuro, translucidez nativa e sete preferências nativas de animação.
- Biblioteca PNG/JPEG/BMP e imagem da tela de bloqueio pela API do Windows, com escopo de restauração descrito em LOCK_SCREEN.md.
- Três cenas procedurais animadas com renderizador Win32/GDI próprio. Limite configurado de 24 quadros por segundo; pausa em bateria e diante de aplicativo em tela cheia. O arranjo WorkerW do Explorer não é documentado como contrato público e pode ficar indisponível em certas versões. A falha é apresentada, sem cobrir ícones ou aplicativos com uma janela substituta.
- Dock flutuante e barra lateral próprios, com atalhos fixos, relógio e opção explícita de ocultar temporariamente a barra nativa. O estado das barras afetadas é guardado para restaurá-las ao encerrar. A aparência interativa desses processos não foi ativada nesta sessão.
- Preferências do usuário para Modo de Jogo, captura histórica de jogos, extensões/arquivos ocultos, Explorer compacto, alinhamento/busca da barra e resposta de menus. Algumas mudanças só aparecem após sair e entrar no Windows.
- Inventário gráfico real e preferência por aplicativo em execução ou executável adicionado manualmente. Adicionar um caminho não executa o arquivo. O campo GpuPreference é atualizado preservando os outros campos da string e o valor/tipo/ausência anterior. Reabrir o aplicativo é necessário; salvar essa preferência não mede qual GPU está renderizando.
- Diagnóstico de integridade com DISM/SFC, reparo explícito com DISM RestoreHealth/SFC scannow, análise/manutenção de armazenamento escolhida pelo Windows e diagnóstico DNS/TCP. São ferramentas do sistema operacional, sem download de um otimizador externo.

## Execução

Preferências, temas, wallpapers e perfis gráficos passam pelo diário existente, com verificação e restauração. Manutenções demoradas têm processos de trabalho separados, saída real, estados persistidos e recusa de repetição da mesma execução. A elevação UAC ocorre somente quando a pessoa inicia uma manutenção que exige administrador. As operações e argumentos são definidos no código; não há endpoint para executar um comando fornecido pelo navegador.

O launcher despacha os workers antes de importar o servidor, inclusive no executável PyInstaller. Cenas e layouts iniciam parados. Nenhuma cena é colocada na inicialização do Windows automaticamente.

Se uma edição anterior estiver aberta, o launcher procura outra porta local e mantém a instância anterior intacta. Uma instância com o mesmo build é reutilizada.

## Evidência e limites

A suíte cobre o ciclo HTTP de aplicar/restaurar, lotes inválidos antes de qualquer escrita, origens externas, importação de imagens, preservação de campos de GPU, restauração após fechar o app, tokens/PIDs de workers, estados de falha e recuperação das barras. Os adaptadores mutáveis usam dublês com dados temporários; consultas reais de hardware, preferências e WinRT foram somente leitura. Não se afirma que os efeitos gráficos foram validados visualmente no desktop real ou que reparo de integridade melhora qualquer jogo.

Fontes técnicas: [SystemParametersInfoW](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-systemparametersinfow), [Optimize-Volume](https://learn.microsoft.com/en-us/powershell/module/storage/optimize-volume), [reparo de imagem do Windows](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/repair-a-windows-image), [preferência gráfica usada por projeto upstream](https://github.com/Legend-of-Dragoon-Modding/Severed-Chains/blob/main/gpu-optional-setup.bat). A persistência gráfica por registro é uma integração dependente de versão, não um setter público de GPU da Microsoft.
