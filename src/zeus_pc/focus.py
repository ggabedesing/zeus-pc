"""The short path: measure, propose only supported changes, verify, undo."""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone

from .desktop import quick_snapshot, read_ac
from .cleanup import clean_temp, temp_root

BALANCED = '381b4222-f694-41f0-9685-ff5bb260df2e'
PERFORMANCE = '8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c'
GOALS = {'daily', 'work', 'gaming'}


def build_report(snapshot: dict, goal: str) -> dict:
    if goal not in GOALS:
        raise ValueError('Escolha dia a dia, trabalho ou jogos.')
    actions, notices = [], []
    supported = snapshot.get('supported') is True
    if supported:
        if goal == 'gaming':
            measured = snapshot.get('preferences', {})
            for id, current, desired, title, description in (
                ('game_mode', False, True, 'Ativar Modo de Jogo', 'Salvar a preferência de Modo de Jogo do Windows.'),
                ('background_capture', True, False, 'Desativar replay em segundo plano', 'Desligar a preferência de captura histórica quando você dispensa replay.'),
            ):
                value = measured.get(id, {})
                if value.get('available') and value.get('value') is current:
                    actions.append({'id': id, 'action_id': 'SET_WINDOWS_PREFERENCE',
                        'args': {'id': id, 'value': desired}, 'title': title, 'description': description,
                        'tradeoff': 'Preferência reversível. O efeito depende do jogo e da versão do Windows.'})
        if snapshot.get('temp_bytes', 0) > 0:
            actions.append({
                'id': 'cleanup', 'action_id': 'CLEAN_USER_TEMP', 'args': {},
                'title': 'Liberar espaço de arquivos temporários',
                'description': f"Remover {snapshot.get('temp_count', 0)} arquivos sem alteração há mais de 7 dias na pasta temporária do usuário.",
                'tradeoff': 'Exclusão permanente. Arquivos alterados ou bloqueados serão preservados.',
                'bytes': snapshot['temp_bytes'], 'reversible': False, 'default': True,
            })
        target = PERFORMANCE if goal in {'gaming', 'work'} and snapshot.get('ac_connected') is True else BALANCED
        plan = next((p for p in snapshot.get('power_plans', []) if p.get('guid') == target), None)
        if plan and not plan.get('active'):
            actions.append({
                'id': 'energy', 'action_id': 'SET_ACTIVE_POWER_PLAN', 'args': {'guid': target},
                'title': 'Priorizar desempenho' if target == PERFORMANCE else 'Equilibrar energia e desempenho',
                'description': f"Ativar o plano {plan['name']} que já existe no seu Windows.",
                'tradeoff': 'Pode aumentar o consumo, o calor e o ruído.' if target == PERFORMANCE else 'Ajusta o consumo à demanda do computador.',
            })
        if snapshot.get('animations_enabled') is True:
            actions.append({
                'id': 'animations', 'action_id': 'SET_UI_ANIMATIONS', 'args': {'enabled': False},
                'title': 'Reduzir animações da interface',
                'description': 'Desativar efeitos de animação na área de conteúdo dos aplicativos compatíveis.',
                'tradeoff': 'A navegação fica mais direta, com menos transições visuais.',
            })
        for index, entry in enumerate(snapshot.get('startup', [])):
            if entry.get('enabled'):
                actions.append({
                    'id': f'startup-{index}', 'action_id': 'SET_STARTUP_ENABLED',
                    'args': {'name': entry['name'], 'enabled': False},
                    'title': f"Não iniciar {entry['name']} com o Windows",
                    'description': 'O aplicativo continua instalado e pode ser aberto quando você precisar.',
                    'tradeoff': 'Escolha apenas os aplicativos que você dispensa ao ligar o PC.',
                    'default': False, 'category': 'startup',
                })
        if (snapshot.get('ram_used_pct') or 0) >= 85:
            notices.append({'id': 'memory', 'title': 'A memória está sob pressão',
                            'description': 'Feche aplicativos que você não está usando. O Zeus preserva os programas abertos e seus arquivos.',
                            'uri': 'taskmgr', 'label': 'Ver aplicativos abertos'})
        if snapshot.get('disk_free_pct') is not None and snapshot['disk_free_pct'] < 15:
            notices.append({'id': 'storage', 'title': 'Seu disco precisa de espaço',
                            'description': 'Revise os arquivos temporários nas configurações de armazenamento do Windows.',
                            'uri': 'storage', 'label': 'Revisar armazenamento'})
    return {
        'id': str(uuid.uuid4()), 'created_at': time.time(), 'goal': goal,
        'status': 'unsupported' if not supported else ('opportunities' if actions else 'ready'),
        'snapshot': snapshot, 'actions': actions, 'notices': notices,
    }


class FocusService:
    def __init__(self, database, transactions, collector=quick_snapshot):
        self.db, self.transactions, self.collector = database, transactions, collector
        self.lock = threading.RLock()
        self.active_run = None

    def analyze(self, goal):
        with self.lock:
            report = build_report(self.collector(), goal)
            self.db.set_setting('focus_report', report)
            return report

    @staticmethod
    def public_report(report):
        if not report:
            return None
        return {**report, 'snapshot': {k: v for k, v in report['snapshot'].items() if not k.startswith('_')}}

    def history(self):
        runs = self.db.get_setting('focus_runs', [])
        changed = False
        for run in runs:
            if run['id'] == self.active_run:
                continue
            if run['status'] == 'running':
                run['status'] = 'partial'
                changed = True
            for item in run['results']:
                if not item.get('ok'):
                    transaction = self._recover_transaction(item.get('transaction_id'))
                    if transaction and transaction['state'] == 'VERIFIED':
                        item.update(ok=True, state='VERIFIED', message='Alteração confirmada no registro da execução recuperada.')
                        changed = True
                    elif run['status'] == 'partial' and not item.get('message'):
                        item['message'] = 'Execução interrompida. Faça uma nova análise para conferir o estado atual.'
                        changed = True
            for item in run.get('undo_results', []):
                if not item.get('ok') and item.get('transaction_id'):
                    tx = self._recover_transaction(item['transaction_id'])
                    if tx and tx['state'] == 'VERIFIED':
                        item.update(ok=True, message='Restauração confirmada após reiniciar o Zeus.')
                        changed = True
            originals = {r['transaction_id'] for r in run['results'] if r.get('ok') and r.get('transaction_id')}
            restored = {r['original_id'] for r in run.get('undo_results', []) if r.get('ok')}
            if originals and originals <= restored and not run.get('undone'):
                run['undone'] = True
                changed = True
        if changed:
            self.db.set_setting('focus_runs', runs)
        return runs

    def _recover_transaction(self, transaction_id):
        transaction = self.db.get_transaction(transaction_id) if transaction_id else None
        if not transaction or transaction['state'] not in {'APPLYING', 'APPLIED'}:
            return transaction
        definition = self.transactions.registry.get(transaction['action_id'])
        if not definition:
            return transaction
        # A process may have died after the OS accepted a write. Re-read only;
        # never replay the original action to discover whether it happened.
        try:
            evidence = definition.verify(transaction['args'])
            if evidence.get('ok'):
                self.db.transition_transaction(
                    transaction_id, from_state=transaction['state'], to_state='VERIFIED',
                    updated_at=datetime.now(timezone.utc).isoformat(), verify=evidence,
                )
                return self.db.get_transaction(transaction_id)
        except Exception:
            pass
        return transaction

    def _save(self, run):
        runs = [r for r in self.history() if r['id'] != run['id']]
        self.db.set_setting('focus_runs', [run, *runs][:30])

    def apply_custom(self, actions, goal='personalization'):
        with self.lock:
            report = {'id': str(uuid.uuid4()), 'created_at': time.time(), 'goal': goal,
                      'snapshot': {}, 'actions': actions}
            return self.apply(report['id'], [a['id'] for a in actions], custom_report=report)

    def apply(self, report_id, action_ids, *, custom_report=None):
        with self.lock:
            previous = next((r for r in self.history() if r['id'] == report_id), None)
            if previous:
                return previous  # Lost HTTP response must never replay a mutation.
            report = custom_report or self.db.get_setting('focus_report')
            if not report or report['id'] != report_id or time.time() - report['created_at'] > 300:
                raise ValueError('A análise expirou. Analise novamente antes de aplicar.')
            allowed = {a['id']: a for a in report['actions']}
            if not isinstance(action_ids, list) or not action_ids or any(not isinstance(a, str) or a not in allowed for a in action_ids):
                raise ValueError('Seleção de ajustes inválida.')
            if len(action_ids) != len(set(action_ids)):
                raise ValueError('Ajustes duplicados.')
            run = {'id': report_id, 'created_at': time.time(), 'goal': report['goal'],
                   'before': {k: v for k, v in report['snapshot'].items() if not k.startswith('_')}, 'after': None, 'status': 'running',
                   'results': [], 'undone': False}
            self.active_run = report_id
            self._save(run)
            for key in action_ids:
                action = allowed[key]
                item = {'title': action['title'], 'ok': False, 'transaction_id': None,
                        'reversible': action.get('reversible', True)}
                run['results'].append(item)
                self._save(run)
                try:
                    if action['action_id'] == 'CLEAN_USER_TEMP':
                        item.update(clean_temp(temp_root(), report['snapshot'].get('_temp_manifest', [])))
                        self._save(run)
                        continue
                    if action['args'].get('guid') == PERFORMANCE and read_ac() is not True:
                        raise ValueError('Conecte o computador à tomada e analise novamente.')
                    prepared = self.transactions.prepare(action['action_id'], action['args'])
                    item['transaction_id'] = prepared.transaction_id
                    self._save(run)
                    self.transactions.consume_consent(prepared.transaction_id, prepared.consent_token)
                    result = self.transactions.execute(prepared.transaction_id)
                    message = (result['transaction'].get('apply') or {}).get('message') or result['message']
                    if result['ok'] and action.get('restart'):
                        message += ' Algumas superfícies só atualizam após sair e entrar no Windows.'
                    item.update(ok=result['ok'], message=message, state=result['state'])
                except Exception as exc:
                    item.update(message=str(exc), state='FAILED')
                self._save(run)
                if not item['ok']:
                    break
            run['status'] = 'verified' if all(r['ok'] for r in run['results']) else 'partial'
            try:
                run['after'] = {k: v for k, v in self.collector().items() if not k.startswith('_')}
            except Exception:
                pass  # Applied state is already journaled even if this read fails.
            self._save(run)
            self.active_run = None
            return run

    def undo(self, report_id):
        with self.lock:
            run = next((r for r in self.history() if r['id'] == report_id), None)
            if not run:
                raise ValueError('Otimização não encontrada.')
            if run.get('undone'):
                return run
            self.active_run = report_id
            undo_results = run.setdefault('undo_results', [])
            done = {r['original_id'] for r in undo_results if r['ok']}
            for item in reversed(run['results']):
                tid = item.get('transaction_id')
                if not tid or tid in done:
                    continue
                transaction = self.db.get_transaction(tid)
                if not transaction or transaction['state'] != 'VERIFIED':
                    continue
                result = {'original_id': tid, 'ok': False}
                try:
                    definition = self.transactions.registry.get(transaction['action_id'])
                    if not self.transactions.verify_target(transaction['action_id'], transaction['args']).get('ok'):
                        raise ValueError('O ajuste mudou depois da otimização. Mantivemos a configuração atual.')
                    inverse = definition.rollback_preview(transaction['before_state'])
                    prepared = self.transactions.prepare(inverse['action_id'], inverse['args'])
                    result['transaction_id'] = prepared.transaction_id
                    undo_results.append(result)
                    self._save(run)
                    self.transactions.consume_consent(prepared.transaction_id, prepared.consent_token)
                    executed = self.transactions.execute(prepared.transaction_id)
                    result.update(ok=executed['ok'], message=executed['message'])
                except Exception as exc:
                    result['message'] = str(exc)
                if result not in undo_results:
                    undo_results.append(result)
                self._save(run)
            applicable = {r['transaction_id'] for r in run['results'] if r.get('ok') and r.get('transaction_id')}
            restored = {r['original_id'] for r in undo_results if r['ok']}
            run['undone'] = bool(applicable) and applicable <= restored
            self._save(run)
            self.active_run = None
            return run
