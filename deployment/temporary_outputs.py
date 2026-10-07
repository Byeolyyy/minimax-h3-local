"""Delete only explicitly unsaved jobs, confined to the job directory."""
import json
from pathlib import Path
import re
import shutil
from contextlib import contextmanager


@contextmanager
def server_lock(root: Path):
    """Exclude a second server or standalone cleanup while this server is live."""
    import msvcrt
    path = root / 'studio/server.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as lockfile:
        if path.stat().st_size == 0:
            lockfile.write(b'0')
            lockfile.flush()
        lockfile.seek(0)
        msvcrt.locking(lockfile.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            lockfile.seek(0)
            msvcrt.locking(lockfile.fileno(), msvcrt.LK_UNLCK, 1)


def remove_job_directory(data: Path, job_id: str):
    if not re.fullmatch(r'[0-9a-f]{24}', job_id):
        raise ValueError('Invalid job ID')
    base = data.resolve()
    path = (base / job_id).resolve()
    if path.parent != base:
        raise ValueError('Job directory escapes the output directory')
    if path.exists():
        shutil.rmtree(path)


def cleanup_unsaved_on_disk(data: Path):
    """Call only when generation processes are stopped (startup or shutdown)."""
    removed = []
    if not data.exists():
        return removed
    for folder in data.iterdir():
        if not re.fullmatch(r'[0-9a-f]{24}', folder.name) or folder.resolve().parent != data.resolve():
            continue
        try:
            job = json.loads((folder / 'job.json').read_text(encoding='utf-8'))
            # Older jobs have no flag: never infer consent to delete those.
            if job.get('saved') is False and job.get('id') == folder.name:
                remove_job_directory(data, folder.name)
                removed.append(folder.name)
        except (OSError, ValueError):
            continue
    return removed


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    with server_lock(root):
        removed = cleanup_unsaved_on_disk(root / 'studio/jobs')
    print(f'Removed {len(removed)} unsaved temporary job(s).')
