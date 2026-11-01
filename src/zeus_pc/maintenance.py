"""User-started native Windows maintenance, separate from short UI requests."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time
import uuid

from .app_paths import data_dir
from .powershell import _decode_output

OPERATIONS = {
    'system_scan': {'title': 'Diagnosticar integridade do Windows', 'description': 'Executar DISM ScanHealth e SFC verifyonly. O resultado vem das ferramentas do próprio Windows.', 'requires_admin': True},
    'system_repair': {'title': 'Reparar arquivos do Windows', 'description': 'Executar DISM RestoreHealth e SFC scannow. Pode baixar componentes oficiais e levar vários minutos.', 'requires_admin': True},
    'storage_analyze': {'title': 'Analisar manutenção do disco', 'description': 'Consultar o Windows sobre a necessidade de otimização do volume do sistema.', 'requires_admin': True},
    'storage_optimize': {'title': 'Otimizar armazenamento', 'description': 'O Windows escolhe a manutenção adequada à mídia, como retrim em SSD ou desfragmentação em HDD.', 'requires_admin': True},
    'network_diagnose': {'title': 'Diagnosticar rede e DNS', 'description': 'Ler adaptadores, resolver DNS e testar conexão TCP com www.microsoft.com:443. Não muda a rede.', 'requires_admin': False},
}
_JOB_ID = re.compile(r'^[a-f0-9]{32}$')


def is_admin():
    return os.name == 'nt' and bool(ctypes.windll.shell32.IsUserAnAdmin())


def _system_exe(name):
    return str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / name)


def commands(operation):
    ps = _system_exe(r'WindowsPowerShell\v1.0\powershell.exe')
    prefix = [ps, '-NoProfile', '-NonInteractive', '-Command']
    drive = os.environ.get('SystemDrive', 'C:')
    if not re.fullmatch(r'[A-Za-z]:', drive):
        raise ValueError('Volume do Windows inválido.')
    if operation == 'system_scan':
        return [('Verificando imagem do Windows', [_system_exe('dism.exe'), '/Online', '/Cleanup-Image', '/ScanHealth']),
                ('Verificando arquivos do sistema', [_system_exe('sfc.exe'), '/verifyonly'])]
    if operation == 'system_repair':
        return [('Reparando imagem do Windows', [_system_exe('dism.exe'), '/Online', '/Cleanup-Image', '/RestoreHealth']),
                ('Reparando arquivos do sistema', [_system_exe('sfc.exe'), '/scannow'])]
    if operation in {'storage_analyze', 'storage_optimize'}:
        analyze = ' -Analyze' if operation == 'storage_analyze' else ''
        return [('Analisando armazenamento' if analyze else 'Mantendo armazenamento', prefix + [
            f"$ErrorActionPreference='Stop'; Optimize-Volume -DriveLetter {drive[0]}{analyze} -Verbose -ErrorAction Stop"])]
    if operation == 'network_diagnose':
        return [('Lendo adaptadores', prefix + ['Get-NetAdapter | Select-Object Name,InterfaceDescription,Status,LinkSpeed | Format-List']),
                ('Consultando DNS e conexão TCP', prefix + ["$ErrorActionPreference='Stop'; Resolve-DnsName www.microsoft.com -Type A -DnsOnly -ErrorAction Stop; Test-NetConnection www.microsoft.com -Port 443 -InformationLevel Detailed | Format-List"])]
    raise ValueError('Manutenção não reconhecida.')


def run_command(command, emit):
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags)
    timed_out = threading.Event()
    def terminate():
        timed_out.set()
        process.kill()
    timer = threading.Timer(3600, terminate)
    timer.start()
    try:
        while chunk := process.stdout.read1(2048):
            emit(_decode_output(chunk))
        result = process.wait()
        if timed_out.is_set():
            raise TimeoutError('A operação excedeu uma hora. Consulte a saída registrada.')
        return result
    finally:
        timer.cancel()
        process.stdout.close()
        if process.poll() is None:
            process.kill()
            process.wait()


def _alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value == 259)
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def launch_worker(job_id, directory, elevated):
    if os.name != 'nt':
        raise OSError('Manutenção disponível apenas no Windows.')
    arguments = ['--maintenance-worker', job_id, '--jobs-dir', str(directory)]
    if not getattr(sys, 'frozen', False):
        arguments.insert(0, str(Path(__file__).resolve().parents[2] / 'packaging' / 'run_zeus.py'))
    if elevated:
        shell = ctypes.WinDLL('shell32', use_last_error=True)
        shell.ShellExecuteW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
        shell.ShellExecuteW.restype = ctypes.c_void_p
        result = shell.ShellExecuteW(None, 'runas', sys.executable,
            subprocess.list2cmdline(arguments), str(Path(sys.executable).parent), 0)
        if not result or result <= 32:
            raise OSError('A solicitação de administrador foi cancelada ou não pôde ser aberta.')
    else:
        subprocess.Popen([sys.executable, *arguments], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


class MaintenanceJobs:
    def __init__(self, directory=None, launcher=launch_worker):
        self.directory = Path(directory) if directory is not None else data_dir() / 'maintenance'
        self.launcher = launcher
        self.lock = threading.Lock()

    def _path(self, job_id):
        if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
            raise ValueError('Identificador de manutenção inválido.')
        return self.directory / (job_id + '.json')

    def _write(self, job):
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(job['id'])
        temporary = path.with_suffix('.' + secrets.token_hex(8) + '.tmp')
        temporary.write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8')
        temporary.replace(path)

    def _read(self, job_id):
        return json.loads(self._path(job_id).read_text(encoding='utf-8'))

    def list(self):
        if not self.directory.exists():
            return []
        jobs = []
        for path in self.directory.glob('*.json'):
            try:
                job = self._read(path.stem)
                age = time.time() - job.get('started_at', 0)
                if ((job['state'] == 'queued' and age > 600) or
                        (job['state'] == 'running' and not _alive(job.get('pid')))):
                    job.update(state='failed', message='A manutenção foi interrompida. A saída anterior foi preservada.', ended_at=time.time())
                    self._write(job)
                jobs.append(job)
            except (OSError, ValueError, KeyError):
                continue
        return sorted(jobs, key=lambda item: item['started_at'], reverse=True)[:30]

    def start(self, operation):
        if operation not in OPERATIONS:
            raise ValueError('Manutenção não reconhecida.')
        with self.lock:
            if any(j['state'] in {'queued', 'running'} for j in self.list()):
                raise ValueError('Já existe uma manutenção em andamento. Aguarde o resultado.')
            metadata = OPERATIONS[operation]
            job = {'id': uuid.uuid4().hex, 'operation': operation, 'title': metadata['title'],
                   'state': 'queued', 'stage': 'Aguardando início', 'output': '', 'started_at': time.time(),
                   'message': 'O Windows solicitará administrador.' if metadata['requires_admin'] and not is_admin() else 'Iniciando operação.'}
            self._write(job)
            try:
                self.launcher(job['id'], self.directory, metadata['requires_admin'] and not is_admin())
            except Exception as exc:
                job.update(state='failed', message=str(exc), ended_at=time.time())
                self._write(job)
            return job

    def run(self, job_id, runner=run_command, admin=None):
        job = self._read(job_id)
        if job['state'] != 'queued' or job['operation'] not in OPERATIONS:
            raise ValueError('Esta manutenção não pode ser executada novamente.')
        if admin is None:
            admin = is_admin()
        if OPERATIONS[job['operation']]['requires_admin'] and not admin:
            job.update(state='failed', message='O Windows não concedeu a permissão de administrador.', ended_at=time.time())
            self._write(job)
            return job
        job.update(state='running', pid=os.getpid(), message='Operação em andamento.')
        self._write(job)
        last_write = [0.0]
        def emit(chunk):
            job['output'] = (job['output'] + str(chunk))[-64000:]
            if time.monotonic() - last_write[0] > .25:
                self._write(job)
                last_write[0] = time.monotonic()
        try:
            for title, command in commands(job['operation']):
                job['stage'] = title
                emit('\n' + title + '\n')
                self._write(job)
                code = runner(command, emit)
                if code not in {0, 3010}:
                    raise RuntimeError(f'A ferramenta do Windows terminou com código {code}. Consulte a saída.')
                if code == 3010:
                    job['restart_required'] = True
            job.update(state='succeeded', message='As ferramentas concluíram. Consulte a saída para avaliar o resultado.')
        except Exception as exc:
            job.update(state='failed', message=str(exc))
        job['ended_at'] = time.time()
        self._write(job)
        return job


def worker_main(argv):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--maintenance-worker', required=True)
    parser.add_argument('--jobs-dir', required=True)
    args = parser.parse_args(argv)
    manager = MaintenanceJobs(Path(args.jobs_dir))
    result = manager.run(args.maintenance_worker)
    return 0 if result['state'] == 'succeeded' else 1


if __name__ == '__main__':
    raise SystemExit(worker_main(sys.argv[1:]))
