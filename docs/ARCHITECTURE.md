# Arquitetura do Zeus PC

## Objetivo

O Zeus PC deve ser uma plataforma local de Windows capaz de entender a máquina antes de propor mudanças.

## Pipeline

OBSERVE → INVENTORY → DIAGNOSE → INTERPRET → DECIDE → PROTECT → EXECUTE → VERIFY → LOG → ROLLBACK

## Camadas

- **Probes**: somente leitura. Coletam dados do Windows e hardware.
- **Domain models**: normalizam estados, fontes e métricas.
- **Diagnostic/Rule engine**: transforma fatos em recomendações.
- **Observer**: coleta temporal de baixo custo.
- **Action registry**: somente ações semânticas permitidas.
- **Transactions**: consentimento, BeforeState, APPLY, VERIFY e ROLLBACK.
- **Web UI**: experiência simples; detalhes técnicos ficam sob demanda.

## Regras de segurança

1. A UI nunca envia comandos shell arbitrários.
2. Uma probe que não consegue medir algo retorna indisponibilidade, não “saudável”.
3. UNKNOWN não equivale a GOOD.
4. Uma ação de performance deve ser mensurável quando possível.
5. Drivers vêm de Windows Update ou fabricante oficial.
6. Firmware/BIOS não é atualizado automaticamente.
7. Defender, Firewall, Secure Boot e TPM não são sacrificados por desempenho.
8. Cada ação mutável precisa de verificação independente do estado final.

## Direção

O app deve crescer por famílias de ações: energia, startup, armazenamento, Windows, drivers, rede, processos, serviços, segurança e personalização. A habilitação é progressiva, não em massa.
