# Increment 020: API hardening (Host/Origin/Content-Type) + SQLite concorrente + polish da UI

## Objetivo

Corrigir as duas críticas P0 levantadas na revisão do 019 antes de qualquer
mutação real do agente: a API local aceitava requisições de qualquer
origem/Host, e o SQLite não estava pronto para concorrência real.
Mais a lista de polish da UI (favicon, meta, 404, logo).

## Ameaça endereçada

1. **DNS rebinding**: um site malicioso resolve seu domínio para
   `127.0.0.1` e finge ser dono da API → `Host` que não é loopback é
   negado com **403** em todo método (API e arquivos estáticos).
2. **CSRF de navegador**: `fetch` cross-site ou `Origin: null` (arquivo
   local) → **403** em toda mutação. Origin precisa ser loopback **com a
   porta do servidor**.
3. **`no-cors` com `text/plain`**: navegador consegue mandar corpo JSON
   com Content-Type simples sem preflight → toda mutação com corpo exige
   `application/json` (**415** caso contrário).

O que **não** mudou: clientes sem navegador (curl com JSON, scripts,
o próprio `app.js` que só faz GET) seguem funcionando; rejeições fecham a
conexao (`Connection: close`) e são auditadas como `request_rejected` no
log estruturado. Token de sessão foi avaliado e **adiado**: qualquer
processo local já roda com os privilégios do usuário (ler o token do
stdout não muda isso); o alvo real do guard é o navegador.

## SQLite concorrente (v4, sem bump)

- `PRAGMA journal_mode=WAL` (leitores não bloqueiam o escritor; fallback
  silencioso em filesystems sem suporte) + `busy_timeout=5000` em toda
  conexão: escrituras concorrentes aguardam em vez de falhar com
  "database is locked".
- `_ensure_schema()`: o schema é criado **uma vez por instância**; as
  leituras deixaram de rodar `initialize()` (que reescrevia o schema a
  cada `get_model_install`, `benchmark_scores`, `get_transaction`...).

## UI (lista de polish)

- `web/favicon.svg` novo + `<link rel="icon">`;
- `meta name="description"` e `<title>` descritivo;
- **logo clicável** (`<a href="/">` + CSS `text-decoration:none`);
- **`web/404.html`** servido com status 404 para páginas inexistentes
  (API continua respondendo JSON 404);
- verificado: viewport e media queries já existiam, nenhum link quebrado,
  todos os botões com listener, nenhuma imagem para comprimir
  (CSS já minificado, 2 assets).

## Verificações

- `unittest discover` → **168 testes OK** (15 novos em
  `tests/test_api_hardening.py`: guards de Host/Origin/Content-Type
  contra o Handler real via HTTP, WAL/busy_timeout, ensure-once, 404);
- `compileall` OK;
- smoke HTTP real: Host rebinding 403, Origin cross-site 403,
  `text/plain` 415, JSON válido chega na camada de domínio (409 do
  BrainError), index com título/description/favicon, 404 estático em
  HTML e 404 de API em JSON, logo clicável, `journal_mode=wal`, 4 eventos
  `request_rejected` auditados.

## Comportamento intencional que muda contratos

`POST` sem `Content-Type: application/json` agora recebe **415** (antes
era parseado). Os smokes/scripts antigos que usavam `curl -d` precisam de
`-H "Content-Type: application/json"` — documentado aqui.

## Próximo

Walking skeleton no PC Windows real: 1 GGUF baixado com hash, 1 invoke
medido, 1 transação executada e revertida. Depois, o Increment 021 —
loop do agente.
