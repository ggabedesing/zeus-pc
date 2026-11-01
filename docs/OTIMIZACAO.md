# Zeus: otimizar e ver o resultado

O fluxo principal agora é análise → seleção → execução → resultado. A interface antiga foi substituída por uma tela que chama o motor local; o antigo mecanismo de recomendações e os modelos de IA não participam deste fluxo.

## O que executa

- Limpeza de arquivos regulares da pasta temporária do usuário sem alteração há mais de sete dias. O executor usa o inventário da análise, relê a identidade de cada arquivo e registra quantos bytes e arquivos foram removidos. Arquivos alterados, bloqueados e links são preservados. A exclusão é permanente e fica explícita antes da aplicação.
- Seleção de um plano de energia existente: equilibrado para dia a dia/bateria e alto desempenho para jogos/trabalho na tomada. Nenhum plano é inventado pelo nome. O computador precisa continuar conectado à tomada na aplicação de alto desempenho.
- Redução de animações de conteúdo usando a API nativa do Windows, com releitura e restauração.
- Controle individual das entradas Run do usuário. O comando e o tipo original são preservados em uma chave de backup. Entradas já desativadas pelo Windows não são oferecidas. O Zeus não substitui decisões do Windows StartupApproved.

O histórico registra ações confirmadas, falhas, bytes removidos e leituras de disco antes/depois. Alterações reversíveis podem ser restauradas; a limpeza permanece feita. Leituras de CPU/RAM são fotografias do momento, não um benchmark nem uma promessa de FPS.

## Implementação

`focus.py` conduz o fluxo sem passar pelas regras de recomendações antigas. `desktop.py` faz a leitura curta. `cleanup.py` e `startup.py` executam as novas ações. Energia, animações e inicialização usam o diário de transações existente para verificar e restaurar mudanças.

A análise expira em cinco minutos. O identificador da execução impede duplicar ações quando uma resposta se perde. Após reiniciar, resultados confirmados no diário são recuperados. Por padrão o monitoramento contínuo fica desligado, para o Zeus permanecer ocioso entre as análises.

Frontend fonte: https://github.com/ggabedesing/zeus/tree/codex/zeus-otimizacao. O build compilado fica em `web/`, incluído no executável.

## Verificação

```powershell
$env:PYTHONPATH='src'
python -m unittest discover -s tests
python tests/ui_fixture.py
```

O segundo comando serve uma cópia descartável em http://127.0.0.1:8001. Animações e inicialização são simuladas; a limpeza opera somente nos arquivos de teste criados dentro de TemporaryDirectory. Nenhum ajuste real de Windows é feito por essa fixture.

Para recompilar o executável no Windows: `python -m PyInstaller --noconfirm --distpath dist --workpath build packaging/ZeusPC.spec`.
