"""Delete only explicitly unsaved jobs, confined to the job directory."""
import json
from pathlib import Path
import re
import shutil
import time
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


def discard_job_media(data: Path, job: dict, reason: str):
    """Remove large output files, retaining bounded diagnostics and the outcome."""
    if job.get('saved') is not False or job.get('media_cleaned'):
        return False
    job_id = job['id']
    if not re.fullmatch(r'[0-9a-f]{24}', job_id):
        raise ValueError('Invalid job ID')
    base = data.resolve()
    folder = (base / job_id).resolve()
    output = (folder / 'output').resolve()
    if folder.parent != base or output.parent != folder:
        raise ValueError('Output directory escapes the job directory')
    # Keep the last 64 KiB, including the error, before removing media.
    log = folder / 'run.log'
    if log.is_file() and log.stat().st_size > 65536:
        with log.open('rb') as stream:
            stream.seek(-65536, 2)
            tail = stream.read()
        log.write_bytes(tail)
    if output.exists():
        shutil.rmtree(output)
    updated = dict(job, media_cleaned=True, cleanup_reason=reason,
                   cleaned_at=time.time(), result=None)
    if job['status'] == 'completed':
        updated.update(status='discarded', stage='未保存的预览已清理：' + reason)
    elif job['status'] not in {'failed', 'cancelled', 'interrupted', 'discarded'}:
        updated.update(status='interrupted', stage='程序已停止，生成中断',
                       error='程序退出或重新启动，生成没有完成。', finished=time.time())
    metadata = folder / 'job.json'
    temp = metadata.with_suffix('.tmp')
    temp.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(metadata)
    job.update(updated)
    return True


def prune_diagnostics(data: Path, keep=30):
    records = []
    for folder in data.iterdir():
        if not re.fullmatch(r'[0-9a-f]{24}', folder.name) or folder.resolve().parent != data.resolve():
            continue
        try:
            job = json.loads((folder / 'job.json').read_text(encoding='utf-8'))
            if job.get('saved') is False and job.get('media_cleaned') is True and job.get('id') == folder.name:
                records.append((job.get('cleaned_at', 0), folder.name))
        except (OSError, ValueError):
            continue
    removed = []
    for _, job_id in sorted(records, reverse=True)[keep:]:
        try:
            remove_job_directory(data, job_id)
            removed.append(job_id)
        except OSError:
            continue
    return removed


def cleanup_unsaved_on_disk(data: Path, reason='程序退出或重新启动'):
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
                if discard_job_media(data, job, reason):
                    removed.append(folder.name)
        except (OSError, ValueError):
            continue
    prune_diagnostics(data)
    return removed


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    with server_lock(root):
        removed = cleanup_unsaved_on_disk(root / 'studio/jobs')
    print(f'Removed {len(removed)} unsaved temporary job(s).')
