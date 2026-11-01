# Zeus

Otimização local do Windows em um fluxo: analisar, escolher, aplicar e conferir.

O Zeus remove temporários antigos, controla quais aplicativos do usuário iniciam com o Windows, ajusta energia e reduz animações. A interface mostra o resultado real de cada ação e permite restaurar os ajustes reversíveis pelo histórico.

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
