# Contexto de integração do Zeus

O usuário corrigiu expressamente a direção: quer um otimizador real e direto, sem o catálogo de regras antigo. O motor de Windows foi implementado no repositório zeus-pc e empacotado; esta página de Base44 é sua porta de entrada e distribuição, não um simulador de Windows.

## Entrega pronta

Download do aplicativo completo (ZIP com ZeusPC.exe e instrução curta):
https://media.base44.com/files/public/6ae68b68ff319d9815120560/e3216fa21_Zeus-Windows.zip

O usuário extrai o ZIP, abre ZeusPC.exe e usa a interface local. Não precisa instalar Python/Node, colar inventários, executar scripts no PowerShell ou marcar manualmente ações como concluídas.

## Arquivos anexados

App.tsx, types.ts e zeus.css são a interface implementada e compilada do produto. Use estes arquivos para substituir o painel/catálogo anterior, mantendo a estrutura visual e o português. Adapte somente o encaixe necessário ao template Base44. A variante hospedada deve trocar o destino de download constante `repo` pelo ZIP acima. As dependências usadas são React e lucide-react.

No domínio hospedado, `localHost` é false: o botão oferece o download do aplicativo Windows e as métricas ficam como traços até que a aplicação seja usada localmente. A versão local usa /api/focus/state, /api/focus/analyze, /api/focus/apply e /api/focus/undo. Não substitua essas chamadas por respostas inventadas, não tente chamar localhost de dentro de uma função remota e não gere scripts alternativos.

Remova do app anterior a nota calculada de otimização, os 17 passos genéricos, a análise por colagem e o gerador de PowerShell. A pessoa não deve precisar estudar uma lista técnica para usar o Zeus. A página hospedada pode dizer brevemente que os ajustes são executados pelo aplicativo Windows. O histórico hospedado fica vazio e aponta para o aplicativo local; não é um histórico de execuções reais do PC.

O pacote implementa limpeza de temporários, inicialização do usuário, energia e animações. Não anuncie ajustes de rede, drivers, TRIM ou GPU que não foram implementados. Não prometa percentual de ganho ou ausência universal de risco.

O objetivo é que a pessoa receba um produto utilizável: baixar, abrir e otimizar, com o mesmo desenho do aplicativo real.
