# Zeus

Otimização local do Windows em um fluxo: analisar, escolher, aplicar e conferir.

O Zeus remove temporários antigos, controla a inicialização, ajusta energia e oferece preferências gráficas por aplicativo. O ambiente de desktop reúne relógio, calendário, monitor de CPU/RAM, notas locais, launcher pesquisável e dock com atalhos fixados, todos próprios do Zeus. O Windows Studio também reúne temas, papéis de parede, imagem da tela de bloqueio, cenas animadas, dock/barra lateral próprios e manutenção do sistema. A interface apresenta o resultado de cada ação e permite restaurar os ajustes reversíveis pelo histórico.

Os recursos visuais são do próprio Zeus. Não há instalador de outro personalizador. Veja [o que foi implementado e como foi verificado](docs/WINDOWS_STUDIO.md).

## Abrir

Use o executável ZeusPC.exe. Ele inclui Python e a interface; não exige Node, terminal ou instalação de dependências.

Para executar a partir do código no Windows com Python 3.11 ou superior, abra `start.bat`.

## Desenvolvimento

- Motor local: `src/zeus_pc/`.
- Interface compilada, incluída no executável: `web/`.
- Fonte da interface: https://github.com/ggabedesing/zeus/tree/codex/zeus-otimizacao.
- Fluxo principal: `focus.py`, independente das recomendações e do Model Hub antigos.
- Detalhes, testes e build: [docs/OTIMIZACAO.md](docs/OTIMIZACAO.md).

```powershell
$env:PYTHONPATH='src'
python -m unittest discover -s tests
```

A aplicação usa somente o endereço local `127.0.0.1`. O plano contém alterações encontradas no computador, sem pontuação inventada ou promessa universal de ganho de FPS. A limpeza exclui temporários com mais de sete dias e não pode ser desfeita; esse efeito é mostrado antes de aplicar.
