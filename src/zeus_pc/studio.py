"""Windows Studio orchestration. All writes remain explicit user actions."""
import os
import threading
import time

from . import personalization, windows_preferences, lock_screen
from .maintenance import MaintenanceJobs, OPERATIONS, is_admin
from .probes import gpu_probe


class Studio:
    def __init__(self, focus):
        self.focus = focus
        self.jobs = MaintenanceJobs()
        self._gpu = {'adapters': [], 'applications': []}
        self._gpu_at = 0
        self._gpu_lock = threading.Lock()
        from .desktop_runtime import Runtime as DesktopRuntime
        from .layout_runtime import Runtime as LayoutRuntime
        self.desktop = DesktopRuntime()
        self.layout = LayoutRuntime()

    def catalog(self):
        from .desktop_runtime import catalog as desktop_catalog
        from .layout_runtime import catalog as layout_catalog
        appearance = personalization.catalog()
        appearance['capabilities']['lock_screen_wallpaper'] = True
        return {'preferences': windows_preferences.catalog(), 'personalization': appearance,
                'maintenance': [{'id': id, **entry} for id, entry in OPERATIONS.items()],
                'desktop_runtime': desktop_catalog(), 'layout_runtime': layout_catalog()}

    def gpu(self, refresh=False):
        with self._gpu_lock:
            if not refresh and time.monotonic() - self._gpu_at < 20:
                return dict(self._gpu)
            result = {'adapters': [], 'applications': [], 'preference_note': 'A preferência é salva no Windows e exige reabrir o aplicativo; ela não mede qual GPU está renderizando.'}
            try:
                probe = gpu_probe()
                metrics = {item.name: item.value for item in probe.metrics}
                for adapter in metrics.get('adapters', []):
                    entry = {'name': adapter.get('Name'), 'driver': adapter.get('DriverVersion')}
                    telemetry_name = str(metrics.get('name') or '').strip().casefold()
                    adapter_name = str(adapter.get('Name') or '').strip().casefold()
                    if telemetry_name and telemetry_name == adapter_name:
                        for source, target, factor in [('memory_total_mib', 'vram_gb', 1024),
                                                       ('temperature_c', 'temperature_c', 1), ('utilization_pct', 'utilization_pct', 1)]:
                            try:
                                entry[target] = round(float(metrics[source]) / factor, 1)
                            except (KeyError, ValueError, TypeError):
                                pass
                    result['adapters'].append(entry)
                result['applications'] = windows_preferences.applications(refresh=True)
                present = {item['id'] for item in result['applications']}
                result['applications'].extend(app for app in windows_preferences.registered_applications() if app['id'] not in present)
                if probe.message:
                    result['message'] = probe.message
            except Exception as exc:
                result['error'] = str(exc)
            self._gpu, self._gpu_at = result, time.monotonic()
            return dict(result)

    def runtimes(self):
        desktop, layout = self.desktop.status(), self.layout.status()
        layout['state'] = str(layout.get('state', 'stopped')).lower()
        layout['message'] = layout.get('error') or ('Layout do Zeus em execução.' if layout.get('running') else '')
        layout['active'] = bool(layout.get('running'))
        return {'desktop_runtime': desktop, 'layout_runtime': layout}

    def state(self):
        return {'supported': os.name == 'nt', 'admin': is_admin(),
                'preferences': windows_preferences.states(), 'personalization': personalization.current_state(),
                'lock_screen': lock_screen.current_state(),
                'gpu': self.gpu(), 'jobs': self.jobs.list(),
                **self.runtimes()}

    def apply(self, items):
        if not isinstance(items, list) or not 1 <= len(items) <= 32:
            raise ValueError('Selecione de 1 a 32 ajustes.')
        actions = []
        for index, item in enumerate(items):
            if not isinstance(item, dict) or set(item) != {'id', 'value'}:
                raise ValueError('Ajuste inválido.')
            id, value = item['id'], item['value']
            restart = False
            if id in windows_preferences.SPECS:
                spec = windows_preferences.SPECS[id]
                action_id, args, title = 'SET_WINDOWS_PREFERENCE', {'id': id, 'value': value}, spec['title']
                restart = spec['restart']
            elif id == 'visual_preset':
                action_id, args, title = 'SET_VISUAL_PRESET', {'preset_id': value}, 'Tema visual do Windows'
            elif id == 'wallpaper':
                action_id, args, title = 'SET_DESKTOP_WALLPAPER', {'asset_id': value}, 'Papel de parede do Windows'
            elif id == 'lock_screen_wallpaper':
                action_id, args, title = 'SET_LOCK_SCREEN_WALLPAPER', {'asset_id': value}, 'Imagem da tela de bloqueio'
            elif id == 'gpu_preference':
                action_id, args, title = 'SET_GPU_PREFERENCE', value, 'Preferência gráfica por aplicativo'
            else:
                raise ValueError('Ajuste fora do catálogo do Zeus.')
            definition = self.focus.transactions.registry.get(action_id)
            if definition is None:
                raise ValueError('Módulo de ajuste não está disponível.')
            # Validate the entire batch before changing even its first item.
            normalized = definition.validate_args(args)
            actions.append({'id': f'studio-{index}', 'title': title, 'action_id': action_id,
                            'args': normalized, 'restart': restart})
        run = self.focus.apply_custom(actions)
        return {'run': run, 'state': self.state()}

    def close(self):
        self.desktop.stop()
        self.layout.stop()
