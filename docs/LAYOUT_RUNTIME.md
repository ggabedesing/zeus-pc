# Dock e barra lateral próprios do Zeus

`zeus_pc.layout_runtime` desenha uma janela flutuante do próprio Zeus usando
Tkinter e Win32 públicos. Não instala aplicativos, não modifica Explorer e não
altera a aparência da área segura de senha/PIN. A superfície escura tem cantos
arredondados desenhados, borda suave, glyphs vetoriais próprios, realce ao passar
o mouse e relógio. O arredondamento DWM é uma preferência adicional da nossa
janela quando o Windows oferece suporte; não é aplicado à barra do sistema.

## Contrato

```python
from zeus_pc.layout_runtime import Runtime, catalog

runtime = Runtime()  # data_dir()/layout-runtime; não inicia janela
catalog()           # {"layouts": [{"id":"dock",...}, {"id":"sidebar",...}]}
runtime.status()    # não inicia janela
runtime.start('dock')
runtime.start('sidebar', hide_native_taskbar=False)
runtime.stop()
```

`Runtime(runtime_dir=None)` permite diretório isolado para testes. `start`
aceita somente `dock`/`sidebar` e um booleano estrito. O padrão preserva a barra
nativa visível. Repetir a mesma combinação reaproveita o worker; trocar layout
ou opção encerra o anterior antes de iniciar outro. O dock fica centralizado
na parte inferior da área de trabalho primária; a sidebar fica à esquerda.

Os três métodos retornam:

```json
{
  "supported": true, "running": true, "state": "STARTING",
  "layout": "dock", "hide_native_taskbar": false,
  "native_taskbar_hidden": false, "pid": 1234,
  "error": null, "restore_errors": []
}
```

Estados: `STARTING`, `RUNNING`, `STOPPING`, `STOPPED`, `ERROR`. Iniciar retorna
após criar o processo, portanto `STARTING` não confirma que a janela já foi
desenhada. Consultas subsequentes mostram erro de inicialização se houver.
`status()` também recupera barras ocultadas por um worker que morreu. O token
interno e caminhos de controle não aparecem na resposta pública.

Integração HTTP prevista: `POST /api/studio/layout`, com `command` igual a
`start` ou `stop`, `layout` quando necessário e `hide_native_taskbar` opcional.
O módulo não define endpoint nem executa comandos recebidos de uma API.

## Processo e encerramento

O entrypoint empacotado deve detectar `--layout-worker` antes de importar o
launcher principal e encaminhar os argumentos a `worker_main(argv)`. Em
desenvolvimento o runtime chama o mesmo Python com `-m zeus_pc.layout_runtime`.

```text
--layout-worker dock|sidebar
--runtime-dir <diretório absoluto>
--token <64 caracteres hexadecimais>
--parent-pid <PID>
[--hide-native-taskbar]
```

Há um único `state.json`, token por execução, stopfile com o nome desse token,
gravação atômica de estado e lock de arquivo entre processos. O worker valida
token, layout, PID, estado e opção antes de construir a janela. Importar o
módulo ou consultar o catálogo não importa Tk nem inicia workers.

Fechar pelo botão × ou Escape termina o worker e restaura a barra nativa.
Um timer de 750 ms verifica o stopfile e a identidade do processo pai; se o
Zeus encerrar, o worker fecha sua janela e restaura as barras. O carimbo de
criação do processo impede confundir reutilização de PID com o pai original.
`stop()` aguarda até 2,5 s; se necessário, encerra somente o PID com o carimbo
de criação registrado e depois recupera as barras. O histórico local é uma
área de controle da mesma conta, não uma barreira contra adulteração por outro
processo do usuário.

## Barra nativa opcional

Quando `hide_native_taskbar=True` é explicitamente solicitado, somente barras
identificadas como `Shell_TrayWnd`/`Shell_SecondaryTrayWnd` e visíveis no início
recebem `ShowWindow(SW_HIDE)`. O plano de recuperação é persistido antes de
ocultá-las. Uma barra que já estava invisível fica intocada. A restauração
reconfere HWND, classe, PID e carimbo do processo; mostra com `SW_SHOWNA` apenas
uma janela originalmente visível que ainda corresponda à identidade capturada
e esteja oculta. Explorer reiniciado ou HWND reutilizado não é alterado.

As chamadas de visibilidade são Win32 públicas, mas os nomes de classes do
shell são detalhes de implementação: ocultação é uma opção de melhor esforço
para a sessão, não uma promessa de personalização garantida em toda versão do
Windows. Não se escrevem Registro de autohide, políticas ou preferências da
barra. A área reservada pelo shell pode permanecer mesmo enquanto sua janela
estiver oculta. Se o worker for encerrado à força, a próxima consulta a
`status()` ou `stop()` restaura barras sob sua responsabilidade. Se tanto pai
quanto worker forem encerrados à força, reabrir o Zeus e consultar o status
aciona a recuperação; nenhum processo pode restaurar enquanto todos estão
encerrados. Falhas são expostas em `restore_errors`.

## Atalhos permitidos

Explorer abre `shell:MyComputerFolder`; Ajustes abre `ms-settings:`; o relógio
abre `ms-settings:dateandtime`; Zeus abre somente
`http://127.0.0.1:<ZEUS_PORT ou 8000>/`. Não há comandos de shell, apps de
terceiros, pinagem arbitrária ou entrada de URLs/caminhos na interface. O
relógio e o desenho pertencem à janela do Zeus. A barra, Iniciar e tela de
login do Windows não recebem estilos privados ou substituições.

## Verificação e referências

Os testes usam processos, HWNDs e janela falsos com dados temporários. Cobrem
início explícito, worker único entre instâncias, mudança de layout, restauração
seletiva, PID/HWND reutilizado, término forçado, saída do pai, token inválido e
falha de Tk. Nenhuma janela ou alteração real da barra foi executada durante
o desenvolvimento. A aparência interativa precisa ser validada em uma sessão
iniciada explicitamente pelo usuário.

- [ShowWindow e SW_HIDE/SW_SHOWNA](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-showwindow).
- [IsWindowVisible e WS_VISIBLE](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-iswindowvisible).
- [GetWindowThreadProcessId](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getwindowthreadprocessid).
- [OpenProcess e fechamento de handles](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-openprocess).
- [WaitForSingleObject, inclusive espera não bloqueante](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject).
- [DWM_WINDOW_CORNER_PREFERENCE, Windows 11](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/ne-dwmapi-dwm_window_corner_preference).
