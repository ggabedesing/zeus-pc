# Área de trabalho animada nativa do Zeus

O módulo `zeus_pc.desktop_runtime` contém o controlador e o renderizador do
próprio Zeus. Usa apenas Python/ctypes e as DLLs user32, kernel32 e gdi32 do
Windows. Não instala nem controla aplicativos externos. Importar o módulo,
consultar o catálogo ou consultar o estado não abre janelas. O padrão é parado;
nenhuma preferência de execução automática é criada.

## Integração com o servidor

```python
from zeus_pc.desktop_runtime import Runtime, catalog

desktop = Runtime()  # usa app_paths.data_dir()
catalog()           # {scenes: [{id, name, description, colors}, ...]}
desktop.status()    # leitura; nunca inicia um renderizador
desktop.start('aurora')  # somente após a ação explícita do usuário
desktop.stop()          # pedido cooperativo, sem matar processos
```

`Runtime(base_data_dir)` também aceita um diretório de dados explícito. As cenas
fechadas são `aurora`, `particles` e `waves`. Cena inválida e Windows indisponível
geram `ValueError`. Uma cena diferente exige parar a atual primeiro; repetir o
início da mesma cena ativa não cria outro processo. Falha ao iniciar o processo
retorna estado `failed` com a mensagem.

Estados: `stopped`, `starting`, `running`, `paused`, `stopping`, `failed`.
O campo `running` é verdadeiro para `running`, `paused` e `stopping`; `active`
também inclui `starting` e permite à interface oferecer parada durante o início.
`running` só aparece depois de o processo filho confirmar a criação da janela.
`supported` informa somente Windows, não garante que a convenção WorkerW esteja
disponível. `fps` e `fps_limit` são o **limite configurado de 24**, não uma medição
de quadros reais. Outros campos: `scene`, `pid`, `process_started` (FILETIME de
criação), `updated_at`, `message` e token de propriedade local.

Consultar o estado confirma PID **e** instante de criação para não confundir
um PID reutilizado com o renderizador. Um processo encerrado aparece como
`stopped`; ausência de heartbeat por 12 segundos aparece como `failed`. Ainda
assim, um processo com identidade válida bloqueia um segundo início até parar.
`stop()` grava um pedido com o token da sessão e retorna imediatamente; o
servidor deve consultar o estado para confirmar o fechamento. Não usa
TerminateProcess, taskkill ou sinais por PID. No encerramento normal do servidor,
é recomendável chamar `stop()`.

## Contrato do processo filho e empacotamento

Fonte:

```text
python -m zeus_pc.desktop_runtime --desktop-worker aurora --runtime-dir <diretório-absoluto> --token <32-hex>
```

Executável congelado:

```text
ZeusPC.exe --desktop-worker aurora --runtime-dir <diretório-absoluto> --token <32-hex>
```

O launcher congelado precisa despachar esse argumento **antes** de importar ou
iniciar o servidor ou sua janela normal:

```python
if '--desktop-worker' in sys.argv[1:]:
    from zeus_pc.desktop_runtime import worker_main
    raise SystemExit(worker_main(sys.argv[1:]))
```

O controlador define PYTHONPATH em modo fonte e inicia o filho com argumentos
fechados, sem shell, stdin/stdout/stderr em DEVNULL e CREATE_NO_WINDOW. Os arquivos
`status.json` e `stop.json` ficam em `data_dir/desktop-runtime`; gravações de JSON
são substituições atômicas. Um worker com token antigo não substitui o estado de
uma sessão nova. O filho só executa se houver registro prévio com o mesmo token.

## Renderização e limites de compatibilidade

O renderizador pede ao Explorer a camada WorkerW e procura o WorkerW irmão
posterior à janela que contém SHELLDLL_DefView. A mensagem `0x052C` e esse arranjo
de classes são uma **convenção não documentada do Explorer**, não uma API pública
garantida pela Microsoft. Não há cópia/instalação de aplicativos de terceiros,
injeção de DLL nem hooks no Explorer.

Se não encontrar a camada correta, o Zeus falha explicitamente. Nunca cria uma
janela de tela cheia por cima dos aplicativos ou ícones como alternativa. A
janela criada já é WS_CHILD do WorkerW, sem ativação ou botão na barra de tarefas;
não recebe cliques. Desenho GDI usa bitmap e DC próprios com buffer duplo. Fechar
libera os objetos GDI, o DC e a janela criados pelo Zeus. Não altera o registro de
papel de parede, não destrói o WorkerW e não oculta a janela dos ícones.

A cena cobre o retângulo da área virtual de todos os monitores, inclusive origem
negativa. Mudança de resolução/topologia ou recriação do Explorer encerra a cena
com uma mensagem para iniciar novamente. Os tamanhos e a posição são consultados
do Windows, sem alterar a configuração de monitores.

O loop limita o desenho a 24 quadros por segundo. Uma vez por segundo verifica
bateria e a janela em primeiro plano: quando fora da tomada ou quando um aplicativo
ocupa seu monitor inteiro, suspende os desenhos, mantém o último quadro e continua
recebendo parada e publicando heartbeat. As verificações desconhecidas não são
tratadas como prova de tela cheia ou bateria. O renderizador ainda consome memória
para o bitmap e algum processamento; não é uma otimização de desempenho.

## Validação sem alterar a área de trabalho

`tests/test_desktop_runtime.py` exercita arquivos reais em diretórios temporários,
com processo e renderizador falsos: início/parada, token, identidade, falhas,
reinício do controlador, taxa de quadros e pausa. A única validação Win32 real
consulta a identidade do processo de testes e configura assinaturas ctypes,
sem criar janelas. Nenhuma cena foi iniciada durante a implementação. A aparência
e a compatibilidade com o WorkerW precisam de uma sessão visual autorizada.

Referências primárias de Win32:

- [CreateWindowExW](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-createwindowexw)
- [SetParent e restrições de estilos/DPI](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setparent)
- [EnumWindows](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-enumwindows)
- [BitBlt](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-bitblt)
- [GetSystemMetrics: tela virtual](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getsystemmetrics)
- [GetProcessTimes: identidade de criação](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes)
