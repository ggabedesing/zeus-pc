# Roadmap do Zeus PC

## Fundação
- [x] Repositório limpo
- [x] Servidor local em loopback
- [x] UI simples
- [x] Diagnóstico inicial
- [x] Observer inicial
- [x] Motor inicial de recomendações
- [x] Validação de plano de energia por GUID

## Diagnóstico avançado
- [ ] Drivers e dispositivos
- [ ] TPM e Firewall
- [ ] Rede, DNS, proxy e link
- [ ] Serviços
- [ ] Tarefas agendadas
- [ ] Processos e I/O
- [ ] Integridade Windows
- [ ] Storage reliability
- [ ] Eventos críticos
- [ ] Performance Counters

## Otimização segura
- [x] Transaction Engine persistente
- [x] Consent token
- [x] BeforeState
- [x] VERIFY
- [x] ROLLBACK
- [ ] Action History
- [ ] Startup controlado
- [ ] Limpeza segura com preview
- [ ] Otimização de energia contextual
- [ ] Comparação antes/depois

## Agente local (Zeus Agent)
- [x] Contexto do agente (digest determinístico orçado em tokens)
- [x] Planner de schema fechado (allowlist do ActionRegistry + risco/consentimento)
- [x] Catálogo de ferramentas (ToolSpec + JSON Schema + /api/agent/tools)
- [x] Hub de modelos locais (ModelSpec fechado + runtime loopback + roteador determinístico + /api/brain/*)
- [x] zeus doctor (saúde: banco, powercfg, llama-server, GGUFs e RAM)
- [x] Download/import de GGUF com hash SHA-256 + aceite de licença (SQLite v4)
- [x] LRU de storage sob guarda de RAM (preview + confirmação)
- [x] Benchmark fechado JA/EN/PT alimentando o roteador (medido > declarado)
- [x] API local endurecida (Host/Origin/Content-Type) + SQLite WAL + 404 customizado
- [x] Runner do walking skeleton (python -m zeus_pc.skeleton: modelo → serve → invoke → trocar e restaurar plano)
- [ ] Walking skeleton real no PC Windows: transação real já executada e revertida no PC (powercfg + restore VERIFIED em 2026-10-18); falta o GGUF real com invoke medido
- [x] Painel desktop legível (Increment 022): sem JSON no fluxo, veredito na tela inicial, domínios agrupados + decodificação honesta do powercfg (OEM)
- [ ] Inferência local embutida ponta a ponta (modelo baixado, offline, sem chaves de API)
- [ ] Loop do agente (perceber → pensar → decidir → executar → verificar → refletir)
- [ ] Modos Assessor/Operador com consentimento
- [ ] Hooks/veto pré-ação e memória de lições (instincts)
- [ ] Servidor MCP local (Obsidian/Codex)

## Drivers e atualizações
- [ ] Inventário de drivers
- [ ] Discovery de updates
- [ ] Windows Update Center
- [ ] winget software updates
- [ ] Download oficial + hash/assinatura
- [ ] Driver rollback

## Gaming / Streaming
- [ ] Perfil Gaming
- [ ] Perfil Gaming + Streaming
- [ ] Observer por sessão
- [ ] GPU/CPU bottleneck evidence
- [ ] VRAM/RAM pressure
- [ ] FPS/frametime quando fonte confiável existir
- [ ] OBS awareness

## PC Experience
- [x] Polish da UI (favicon, meta description, logo clicável, 404)
- [x] Redesign desktop do painel (sidebar fixa, cards densos, sem JSON cru — Increment 022)
- [ ] Tema do Windows
- [ ] Wallpaper
- [ ] Cores
- [ ] Taskbar/Start quando suportado
- [ ] Sons e animações
- [ ] Branding suportado
- [ ] Preview antes de aplicar
