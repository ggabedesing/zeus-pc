# Ambiente de desktop do Zeus

Em Personalizar, a seção **Seu espaço. Outra sensação.** reúne a configuração do ambiente. A prévia é ilustrativa: CPU e RAM exibidas nela não são medidas do computador. Os widgets nativos usam as leituras reais de GetSystemTimes e GlobalMemoryStatusEx, a cada dois segundos.

## O que abre no Windows

- Relógio com data e calendário do mês.
- Widget de CPU e memória.
- Notas editáveis com até 4000 caracteres, salvas apenas na pasta local de dados do Zeus.
- Dock com Arquivos, Configurações, Zeus e até oito atalhos fixados do Menu Iniciar.
- Barra do ambiente com data e acesso à busca.
- Launcher pesquisável, com busca que ignora acentos, setas/Enter/Esc e atalho Ctrl+Espaço quando disponível. Se o atalho já estiver registrado por outro aplicativo, o Zeus não o toma; a busca continua acessível pelos widgets.

Todos são componentes do próprio Zeus escritos em Tk/Canvas/Win32. As janelas dos widgets não são topmost: aplicativos normais podem cobri-las. O launcher aparece acima quando solicitado. Os widgets são elevados sem ativação quando a área de trabalho do Explorer recebe foco. Isso não substitui o shell do Windows nem transforma o Windows em Hyprland.

## Configuração

Temas Aurora, Grafite, Luz e Essencial compartilham cores entre os componentes. Cor de destaque, opacidade, movimento, densidade, widgets e posições são configuráveis. Arrastar pela faixa superior salva uma posição proporcional à área útil do monitor principal. Ao mudar resolução ou área útil, as posições são limitadas à tela. O editor visual também permite mover os widgets e restaurar a organização inicial.

**Salvar escolhas** altera somente os arquivos do Zeus; um ambiente ativo recebe a mudança. **Ativar meu ambiente** abre o worker identificado pelo mesmo mecanismo do dock anterior. A revisão oferece aplicar tema/fundo do Windows pelo histórico e ocultar temporariamente a barra nativa, ambos desmarcados por padrão. O ambiente inicia desligado e não cria tarefa de inicialização automática. Encerrar fecha o worker e restaura as barras que ele próprio ocultou. Wallpaper/tema aplicados são restaurados pelo Histórico.

Configuração e notas ficam em `data/environment/config.json` (no executável, sob `%LOCALAPPDATA%/ZeusPC`). Escritas usam bloqueio entre processos e substituição atômica. Arquivo inválido é reportado, sem sobrescrevê-lo silenciosamente. O launcher indexa apenas atalhos .lnk das pastas Programs do Menu Iniciar e lança IDs conhecidos cujo arquivo ainda existe; não recebe comandos de terminal do navegador. Atalhos podem abrir programas com seus próprios efeitos normais, como ocorre no Menu Iniciar.

## Verificação

Testes de configuração, busca, argumentos recusados, ciclo HTTP e worker, conflitos de hotkey, persistência de notas, geometria, temporizadores e encerramento. Os testes Tk criam widgets reais mantidos ocultos, com hotkey, camada de janelas e lançamento substituídos; não alteram aparência ou barra do Windows. Validação visual do desktop ativo deve ser distinguida desses testes.

Referências da Microsoft: [RegisterHotKey](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerhotkey) e [SetWindowPos](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowpos).
