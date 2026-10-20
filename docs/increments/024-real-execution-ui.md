# Increment 024 — Execução real na UI (preparar → consentir → executar)

**Data:** 2026-10-20
**Problema apontado pelo usuário:** os botões do app só *mostravam* coisas — nada executava de dentro da interface, mesmo com o backend de transações completo.

## O que mudou

### `web/app.js` — fluxo de transação genérico
- `postJson()` — POST JSON com leitura de `message` de erro do servidor.
- `consentDialog(tx, label)` — modal de consentimento: o que vai mudar (rótulo PT do `action_id`), estado atual (`before_state`), como desfazer (`rollback_preview`), validade do token; botões "Confirmar e aplicar" / "Cancelar".
- `applyPowerPlan(guid, name)` — `POST /api/transactions/prepare` → modal → `consent` → `execute`; status em banner `.tx-status` (✓ verificado / ✗ falha / cancelado).
- `choosePowerPlan()` — seletor de plano inativo (rádio) para quando não há GUID na mão.
- `renderPowerPanel()` — painel Energia re-renderizável, com botão **"Ativar"** por plano inativo e re-render pós-execução.
- Findings do plano de otimização com `action_id = SET_ACTIVE_POWER_PLAN` ganham o botão **"Aplicar com consentimento…"** (seletor de plano → fluxo acima) e atualizam o plano via `/api/optimization/plan`.

### `web/index.html` / `web/styles.css`
- `#planStatus` banner na tela Otimizar; estilos de `.modal-*`, `.tx-status`, `.tx-btn`, `.plan-pick`.

### Backend
- **Nenhuma mudança**: `rules.py` já publicava `action_id` nas recomendações; a engine de transações (prepare/consent/execute + verificação pelo Windows) já existia. Só faltava a UI.

## Verificação (navegador, nesta máquina)
- Sistema → Energia: 4 planos com "Ativar", "Alto desempenho" ativo.
- Clique em Ativar (Desempenho Máximo) → modal com estado atual `8c5e7fda… · Alto desempenho` → Confirmar → **"✓ Execucao aplicada e verificada com sucesso."** → painel re-renderiza com "Desempenho Máximo" **ativo** (mudança real via `powercfg /setactive`).
- Restauração ida e volta: "Alto desempenho" voltou a ficar ativo (máquina intacta).
- Log da aplicação com **2 ciclos completos** `transaction_prepared → transaction_consented → transaction_verified`.
- Console do navegador **vazio** em toda a sessão; Otimizar renderiza com `#planStatus` (0 botões sob perfil GENERAL — a regra de energia só dispara em perfis GAMING, comportamento esperado).
- Suíte Python inalterada: **204 testes OK** (nenhum arquivo `.py` mudou neste incremento).

## Limitações / próximos passos
- "Como desfazer" mostra `SET_ACTIVE_POWER_PLAN` quando o `rollback_preview` só tem `action_id`+`args` aninhados — melhorar para "voltar para <plano>".
- Só existe hoje **uma** ação executável (`SET_ACTIVE_POWER_PLAN`); a paridade IObit (drivers, limpeza, inicialização, winget) precisa de novas `ActionDefinition`s no mesmo fluxo de consentimento — ver direção registrada no Increment 023.
