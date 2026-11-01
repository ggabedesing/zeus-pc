# Imagem da tela de bloqueio

`lock_screen.py` usa o WinRT fornecido pelo Windows, por meio do Windows PowerShell e System.Runtime.WindowsRuntime já presentes no sistema. Não instala um personalizador ou serviço externo.

A disponibilidade exige `UserProfilePersonalizationSettings.IsSupported()` e leitura bem-sucedida da imagem atual por `LockScreen.GetImageStream()`. O Zeus registra essa imagem na biblioteca local antes de alterar. IDs de assets e snapshots são fechados; o navegador não fornece caminhos de arquivos nem código PowerShell.

`TrySetLockScreenImageAsync()` recebe uma cópia gerenciada com nome único. O retorno precisa ser verdadeiro; o resultado é lido novamente e recebe uma impressão SHA-256. A verificação independente relê o stream do Windows e confere essa impressão, pois o Windows pode transformar a codificação da imagem original. A restauração utiliza a imagem capturada e também verifica a imagem resultante.

A restauração é da imagem estática. Definir uma imagem pode substituir Spotlight ou apresentação de slides; restaurar os pixels anteriores não reconstitui a seleção do provedor anterior. O diálogo do aplicativo explica esse efeito. O formulário de credenciais e a senha continuam sendo geridos pelo Windows.

Os testes usam adaptador falso e dados temporários. Uma consulta WinRT real confirmou apenas a disponibilidade de leitura nesta máquina; a tela de bloqueio real não foi alterada nesta sessão.

- [TrySetLockScreenImageAsync](https://learn.microsoft.com/en-us/uwp/api/windows.system.userprofile.userprofilepersonalizationsettings.trysetlockscreenimageasync)
- [LockScreen.GetImageStream](https://learn.microsoft.com/en-us/uwp/api/windows.system.userprofile.lockscreen.getimagestream)
- [LockScreen.OriginalImageFile](https://learn.microsoft.com/en-us/uwp/api/windows.system.userprofile.lockscreen.originalimagefile)
