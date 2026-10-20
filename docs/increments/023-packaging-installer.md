# Increment 023 — Packaging: ZeusPC.exe + instalador por usuário

**Data:** 2026-10-20
**Objetivo:** entregar o "exe para instalar" do roadmap (*Packaging and release*): um `ZeusPC.exe` que sobe sozinho e um instalador com atalho e desinstalação, sem UAC.

## O que mudou

### Caminhos frozen-aware — `src/zeus_pc/app_paths.py` (novo)
- O PyInstaller extrai o bundle para `%TEMP%\_MEIxxxx`, que some ao fechar; `Path(__file__).parents[2]` apontaria para lá (SQLite e UI perdidos).
- `bundle_dir()`: raiz do repositório em dev, `sys._MEIPASS` no EXE (fallback: pasta do EXE, modo onedir).
- `web_dir()` vem do bundle; `data_dir()` vai para `%LOCALAPPDATA%\ZeusPC\data` no EXE (SQLite, logs, modelos) e `data/` no repositório em dev.
- Override único `ZEUS_DATA_DIR` vale em qualquer modo (testes/portátil/diagnóstico).
- Ligado em `server.py` (WEB/DATA), `doctor.py`, `skeleton.py` e `brain/runtime.py` (`models_dir()` dinâmico).

### Launcher — `src/zeus_pc/launcher.py` (novo) + `packaging/run_zeus.py`
- `server.py` agora expõe `create_server()` / `finalize_server()`; o `main()` de `python -m zeus_pc.server` continua igual.
- Checagem de porta: já existe um Zeus no ar → só abre o navegador (instância única, exit 0); outro programa na porta → diálogo de erro e exit 1.
- Sobe o servidor em thread, espera `/api/status` responder, abre o navegador e mostra a janela de status (Tk): "Abrir painel" e "Parar e sair"; fechar a janela encerra o servidor.
- Sem console (janela PyInstaller): stdout/stderr vão para `data/zeus-launcher.log`; exceções gravam traceback no mesmo arquivo + MessageBox no Windows.

### Build — `packaging/make_icon.py`, `packaging/ZeusPC.spec`
- Ícone do brand gerado só com stdlib (quadrado verde `#66e08a → #3fae68` + Z branco; 16/32/48/256; DIB/BMP32 com supersampling e mix alpha).
- `pyinstaller --onefile --windowed --name ZeusPC --add-data "../web;web" --icon zeus.ico` (datas e ícone são resolvidos **relativos à pasta do spec**) → `dist/ZeusPC.exe` (**12,6 MB**).

### Instalador — `packaging/zeus-pc.iss` (Inno Setup 6.7.3 via winget, escopo usuário)
- `PrivilegesRequired=lowest`: instala em `%LOCALAPPDATA%\Programs\ZeusPC` **sem UAC**.
- Atalho no Menu Iniciar (grupo "Zeus PC"); tarefas desmarcadas: atalho na área de trabalho e "iniciar com o Windows" (HKCU\...\Run com `uninsdeletevalue`).
- Interface em português do Brasil (`BrazilianPortuguese.isl`); desinstalador padrão na Lista de Programas.
- **Dados preservados na desinstalação** (`%LOCALAPPDATA%\ZeusPC\data`): são o histórico de análises da máquina.
- Compilação: `ISCC.exe packaging\zeus-pc.iss` → `dist/ZeusPC-Setup-0.1.0.exe` (**14,3 MB**).

## Verificação executada (real, após todos os edits)
- **204 testes OK** (17 novos: `tests/test_app_paths.py` ×8, `tests/test_launcher.py` ×9) + `compileall` OK.
- EXE real: boot → `/api/status` **200** (schema v4, writable), `/` serve o bundle ("Painel" ×2), `styles.css` **200** `text/css`.
- Dados criados em `%LOCALAPPDATA%\ZeusPC\data` (zeus.db, zeus-app.jsonl, zeus-launcher.log) — fora do bundle; log sem nenhum traceback.
- Instância única: segunda abertura → **exit 0** + "ja em execucao" no log.
- Scan real pelo EXE: `GET /api/scan` → **200 em 33 s**, histórico persistido (id=1) no banco do LOCALAPPDATA.
- Ciclo completo do instalador: instalar (`/VERYSILENT`, exit 0, log "Installation process succeeded") → exe instalado sobe (**200** + UI íntegra) → atalho do Menu Iniciar criado → **sem** entrada no auto-start → desinstalar (`/VERYSILENT`) → exe, atalho e registro removidos, **dados preservados**, nenhum processo sobrando.

## Limitações
- EXE e instalador **não assinados**: SmartScreen/antivírus podem alertar na primeira execução (certificado de assinatura é o próximo passo de release).
- GGUF/llama-server seguem pendentes do Increment 021 (`model/serve/invoke` = pulado).
- A janela Tk é o controle único do processo; minimizar para bandeja exigiria lib de terceiros.

## Rebuild rápido
```bash
python packaging/make_icon.py
python -m PyInstaller --noconfirm --onefile --windowed --name ZeusPC --paths src \
  --add-data "../web;web" --icon zeus.ico --specpath packaging packaging/run_zeus.py
"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" packaging\zeus-pc.iss
```

## Direção nova registrada (2026-10-20)
Paridade de funções com a suíte IObit — **Driver Booster primeiro** (o roadmap "Drivers e atualizações" já traz inventário → discovery → Windows Update → winget → download com hash → rollback; o inventário de drivers existe parcialmente na camada de recomendações `drivers.unsigned-review.v1`). Depois, utilitários no estilo Advanced SystemCare.
