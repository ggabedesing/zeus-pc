# Personalização nativa do Zeus

O módulo `zeus_pc.personalization` oferece quatro perfis: Grafite, Aurora,
Luz e Essencial. Cada perfil combina wallpaper abstrato estático gerado
localmente, preferência clara/escura para Windows e aplicativos, translucidez
nativa e sete preferências de animação. Não exige download nem biblioteca de
imagem. As cores de preview representam o wallpaper e a interface do Zeus;
não prometem pintar todos os aplicativos ou substituir o shell do Windows.

## Interfaces para integração

- `register_actions(registry)`: registra ações reais, reversíveis e com schema.
  Importa `ActionDefinition` dentro da função para permitir chamada durante a
  construção do registro padrão sem importação circular.
- `catalog()`: retorna `presets`, `wallpapers`, `capabilities`. Cada preset
  contém `id`, `name`, `description`, `theme`, `transparency`, `motion`,
  `animations`, `colors` (`background`, `surface`, `accent`, `text`) e
  `wallpaper_id`. Cada wallpaper contém `id`, `name`, `format`, `width`,
  `height`, `bytes`, `builtin` (o retorno imediato de importação omite builtin).
  A primeira consulta gera quatro BMPs 960×540 em dados locais; não altera
  nenhuma preferência do Windows.
- `current_state()`: leitura de `supported`, `theme` (`light`, `dark`, `mixed`),
  `transparency`, `animations`, `wallpaper`, `read_errors`. Quando disponíveis,
  `registry` inclui os estados exatos das preferências de tema. Valores
  ausentes não são confundidos com zero. `wallpaper` é uma informação de
  leitura, nunca um caminho que possa ser passado à ação pública.
- `import_wallpaper(data: bytes)`: importa bytes PNG/JPEG/BMP e retorna metadata.
- `wallpaper_asset(asset_id) -> Path`: resolve apenas arquivo gerenciado,
  revalida formato, tamanho e hash antes de servir ou aplicar.

As ações públicas aceitam somente:

```json
{"action_id":"SET_VISUAL_PRESET","args":{"preset_id":"aurora"}}
{"action_id":"SET_DESKTOP_WALLPAPER","args":{"asset_id":"<sha256 de 64 caracteres>"}}
```

Ambas usam `WINDOWS_PERSONALIZATION` como recurso exclusivo. A ação de preset
inclui seu wallpaper. A ação de wallpaper usa preenchimento (`WallpaperStyle=10`,
`TileWallpaper=0`) e não muda tema ou animações. A integração deve passar pelo
fluxo de preparação, consentimento e execução do TransactionEngine.

## Captura, falhas e restauração

Cada ação captura o caminho SPI anterior e os valores HKCU envolvidos, com
existência, tipo e conteúdo exatos. Capturas de perfil também incluem todos os
estados de animação e os valores `UserPreferencesMask` e `MinAnimate`. Valores
binários são preservados em base64 no JSON. A restauração chama SPI primeiro e
restaura os valores do Registro em seguida, inclusive removendo valores que não
existiam antes; não remove chaves nem valores não envolvidos na ação.

Snapshots locais ficam em `data_dir()/personalization-snapshots`. Os previews
apontam para `RESTORE_VISUAL_STATE` ou `RESTORE_DESKTOP_WALLPAPER`, com apenas
`snapshot_id` aleatório. Estas ações também capturam seu próprio estado anterior
e podem ser desfeitas. O cliente não fornece valores de Registro nem caminhos
de wallpaper para restauração. Não excluir snapshots enquanto houver histórico
que dependa deles. Eles pertencem ao mesmo limite de confiança local do banco
de transações; não são um mecanismo contra adulteração por outro processo da
mesma conta.

Aplicações parcialmente falhas tentam restaurar imediatamente seu estado de
entrada e informam se a recuperação foi confirmada. Verificações independentes
releem SPI e HKCU. Um wallpaper anterior removido por outro programa ou política
corporativa pode impedir uma restauração; a falha é reportada. O módulo não
interrompe Explorer ou aplicativos para forçar atualização. Algumas aplicações
só refletem tema após serem reabertas; políticas podem sobrepor preferências.

## Armazenamento de imagens

Arquivos ficam em `data_dir()/wallpapers/<sha256>.<ext>`. O nome original do
usuário não é utilizado. Limites: 20 MiB, lado de até 16.384 pixels e até
100 milhões de pixels. A importação verifica assinaturas e cabeçalhos,
dimensões, CRC do IHDR PNG, final PNG/JPEG e tamanho/dados RGB de BMP. BMPs
importados devem ser RGB 24/32 bits sem compressão. Isso não substitui um
decoder completo; o Windows ainda pode recusar um arquivo comprimido com
conteúdo inválido, e a ação reporta o erro. IDs desconhecidos, caminhos,
argumentos extras, symlinks de assets e conteúdo alterado são recusados.

## O que o Windows oferece

`SystemParametersInfoW` é usado para wallpaper, animações de área de cliente,
menus, fade de menu, tooltip, fade de tooltip, fade de seleção e minimizar/
restaurar. Flags de fade são escritas com suas animações principais ativas e
as preferências principais são recolocadas por último, pois o Windows ignora
certos fades quando a animação correspondente está desligada. O perfil
Essencial desliga os sete efeitos. Outros perfis os habilitam.

As três preferências de cor documentadas em HKCU `Themes/Personalize` são
`AppsUseLightTheme`, `SystemUsesLightTheme` e `EnableTransparency`. Uma
notificação `WM_SETTINGCHANGE` com timeout informa aplicações. Transparência
significa a translucidez nativa disponível na versão do Windows; o Zeus não
injeta estilos, não usa APIs privadas de composição da barra e não troca a
disposição da barra de tarefas, menu Iniciar ou credenciais.

O WinRT público `UserProfilePersonalizationSettings` possui
`IsSupported()` e `TrySetLockScreenImageAsync(StorageFile)`, com retorno que
precisa ser conferido e a exigência de nomes diferentes para imagens alteradas.
Isso torna o wallpaper da tela de bloqueio uma integração futura viável,
condicionada à disponibilidade no dispositivo, ao empacotamento e a uma captura
anterior restaurável. Este módulo não anuncia nem registra essa alteração.
A tela segura de entrada, campos de senha/PIN e credential providers ficam
fora do escopo e não são alterados.

## Fontes oficiais consultadas

- [SystemParametersInfoW: operações, parâmetros e persistência](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-systemparametersinfow).
- [ANIMATIONINFO: minimizar/restaurar](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-animationinfo).
- [Referência Windows 10/11: valores de cores no Registro](https://learn.microsoft.com/en-us/windows/apps/develop/settings/settings-common).
- [WM_SETTINGCHANGE e broadcast de preferências](https://learn.microsoft.com/en-us/windows/win32/winmsg/wm-settingchange).
- [UserProfilePersonalizationSettings: desktop e lock screen](https://learn.microsoft.com/en-us/uwp/api/windows.system.userprofile.userprofilepersonalizationsettings?view=winrt-26100).

## Verificação

`PYTHONPATH=src python -m unittest discover -s tests -p test_personalization.py -v`
exercita arquivos temporários e adaptadores falsos, incluindo ausência e tipo
de valores, inversas executáveis, falha parcial, isolamento de wallpaper,
integridade de assets e fades com animações principais desligadas. Os testes
não aplicam alterações neste PC. Uma consulta nativa somente de leitura pode
ser feita por `current_state()`.
