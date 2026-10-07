"""Small local Chinese interface for the existing H3 installation."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time
from contextlib import asynccontextmanager

import psutil
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from launch_h3 import configure_local_proxy_bypass
from temporary_outputs import cleanup_unsaved_on_disk, discard_job_media, prune_diagnostics, remove_job_directory, server_lock

ROOT = Path(os.environ.get("H3_ROOT", Path(__file__).resolve().parents[1])).resolve()
HERE = Path(__file__).resolve().parent
DATA = ROOT / "studio" / "jobs"
DATA.mkdir(parents=True, exist_ok=True)
PYTHON = ROOT / "env_venv/Scripts/python.exe"
LOCK = threading.RLock()
JOBS: dict[str, dict] = {}
ACTIVE: str | None = None
PROCESS: subprocess.Popen | None = None
WORKER: threading.Thread | None = None
CANCEL = threading.Event()
CLEANER_STOP = threading.Event()
PREVIEW_TTL = 24 * 60 * 60
TERMINAL = {"completed", "failed", "cancelled", "interrupted", "discarded"}
# H3's native 24 fps frame grid: 17*n + 5, minimum 107 frames.
RESOLUTIONS = {"640x384", "832x480", "384x640", "480x832", "512x512"}


class Generation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=20000)
    seconds: float | None = Field(default=None, ge=4.5, allow_inf_nan=False)
    video_length: int | None = Field(default=None, ge=107, strict=True)
    resolution: str = "832x480"
    num_inference_steps: int = Field(default=20, ge=1, le=50, strict=True)
    seed: int | None = Field(default=None, ge=0, le=2147483647, strict=True)


def build_plan(req: Generation) -> dict:
    if not req.prompt.strip():
        raise ValueError("请输入提示词。")
    if req.resolution not in RESOLUTIONS:
        raise ValueError("请选择有效的分辨率。")
    if req.seconds is not None and req.video_length is not None:
        raise ValueError("秒数与帧数只需填写一种。")
    frames = req.video_length
    if frames is None:
        target = (req.seconds if req.seconds is not None else 5) * 24
        if not math.isfinite(target):
            raise ValueError("输入的时长过大。")
        # Match WanGP's nearest native frame count; keep the prompt untouched.
        frames = max(107, ((round(target) - 5 + 8) // 17) * 17 + 5)
    if (frames - 5) % 17:
        raise ValueError("H3 帧数须为 17 的倍数加 5，例如 107、124、243、362。")
    settings = {
        "model_type": "h3_local_heretic", "image_mode": 0,
        "prompt": req.prompt, "resolution": req.resolution,
        "video_length": frames, "num_inference_steps": req.num_inference_steps,
        "sample_solver": "euler", "guidance_scale": 1.0,
        "seed": req.seed if req.seed is not None else secrets.randbelow(2147483647),
        "batch_size": 1, "repeat_generation": 1, "multi_prompts_gen_type": "FG",
        "sliding_window_size": 362, "sliding_window_overlap": 18,
        "prompt_enhancer": "", "activated_loras": [], "loras_multipliers": "",
        "temporal_upsampling": "", "spatial_upsampling": "", "postprocess_audio": "",
        "self_refiner_setting": 0, "custom_settings": {"audio_refinement": "none"},
    }
    return {"settings": settings, "seconds": round(frames / 24, 3),
            "resolution": req.resolution, "steps": req.num_inference_steps}



def job_path(job_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{24}", job_id):
        raise HTTPException(404, "找不到这项任务。")
    path = (DATA / job_id).resolve()
    if path.parent != DATA.resolve():
        raise HTTPException(400, "任务路径无效。")
    return path


def save(job: dict):
    path = job_path(job["id"]) / "job.json"
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def restore_jobs():
    cleanup_unsaved_on_disk(DATA)
    for folder in DATA.iterdir():
        try:
            job = json.loads((folder / "job.json").read_text(encoding="utf-8"))
            if job_path(job['id']) != folder.resolve():
                continue
            job.setdefault('saved', True)
            if job["status"] not in TERMINAL:
                job.update(status="interrupted", stage="上次退出时已停止，不会自动续跑。")
                save(job)
            JOBS[job["id"]] = job
        except (OSError, ValueError, KeyError, HTTPException):
            continue


def stop_process(proc: subprocess.Popen | None):
    if proc is None or proc.poll() is not None:
        return
    try:
        parent = psutil.Process(proc.pid)
        owned = list(reversed(parent.children(recursive=True))) + [parent]
        for child in owned:
            try:
                child.terminate()
            except psutil.Error:
                pass
        _, alive = psutil.wait_procs(owned, timeout=6)
        for child in alive:
            try:
                child.kill()
            except psutil.Error:
                pass
    except psutil.Error:
        pass


def log_tail(job_id: str, length=18000) -> str:
    path = job_path(job_id) / "run.log"
    if not path.exists():
        return ""
    with path.open("rb") as f:
        f.seek(max(0, path.stat().st_size - length))
        return f.read().decode("utf-8", errors="replace")


def public_job(job: dict) -> dict:
    out = dict(job)
    out["elapsed"] = round((job.get("finished") or time.time()) - job["created"])
    if job["status"] in {"running", "cancelling"}:
        tail = log_tail(job["id"])
        windows = re.findall(r"Sliding Window\s+(\d+)/(\d+)", tail)
        if windows:
            out["window"] = [int(x) for x in windows[-1]]
        matches = list(re.finditer(r"\[(\d+)/(\d+)\]\s*(?:Sliding Window\s+\d+/\d+\s*-\s*)?(Denoising|VAE Decoding|Encoding[^\r\n|]*)", tail))
        if matches:
            m = matches[-1]
            current, total, stage = int(m[1]), max(1, int(m[2])), m[3]
            out["stage"] = "生成画面" if stage == "Denoising" else "编码成片" if stage == "VAE Decoding" else "理解画面描述"
            out["step_text"] = f"{current} / {total}"
            out["progress"] = min(.97, .12 + .8 * current / total) if stage == "Denoising" else .96 if stage == "VAE Decoding" else .1
            if windows:
                window, count = (int(x) for x in windows[-1])
                out["progress"] = min(.99, (window - 1 + out["progress"]) / max(1, count))
        elif "Loading" in tail:
            out["stage"] = "加载模型，准备显卡"
            out["progress"] = .04
        if job["status"] == "cancelling":
            out["stage"] = "正在停止并释放显卡"
    return out


def cleanup_temporary_jobs(reason='开始新任务', expired_only=False):
    """Caller holds LOCK; never remove a live worker's files or a saved result."""
    for job_id, job in list(JOBS.items()):
        if expired_only and (job['status'] != 'completed' or time.time() - job.get('finished', time.time()) < PREVIEW_TTL):
            continue
        if job.get('saved') is False and job_id != ACTIVE and job['status'] in TERMINAL:
            try:
                discard_job_media(DATA, job, reason)
            except OSError:
                continue  # A video reader may hold the file; retry on next pass.
    for job_id in prune_diagnostics(DATA):
        JOBS.pop(job_id, None)


def cleanup_abandoned_previews():
    # Browser presence is not proof that a user has abandoned a GPU job.
    # Background timers, sleep, network changes and page reloads are normal.
    with LOCK:
        cleanup_temporary_jobs('完成后超过 24 小时未保存', expired_only=True)


def cleanup_loop():
    while not CLEANER_STOP.wait(3):
        cleanup_abandoned_previews()


def run_generation(job_id: str):
    global ACTIVE, PROCESS
    folder = job_path(job_id)
    job = JOBS[job_id]
    proc = None
    try:
        with LOCK:
            if CANCEL.is_set():
                raise InterruptedError()
            job.update(status="running", stage="加载模型，准备显卡")
            save(job)
        env = dict(os.environ, PYTHONUTF8="1", GRADIO_ANALYTICS_ENABLED="False")
        env.setdefault('HF_HOME', str(ROOT.parent / 'cache/huggingface'))
        env.setdefault('TORCH_HOME', str(ROOT.parent / 'cache/torch'))
        actual_settings = json.loads((folder / 'settings.json').read_text(encoding='utf-8'))
        expected_settings = build_plan(Generation(**job['request']))['settings']
        if actual_settings != expected_settings:
            raise RuntimeError('生成参数与本次提交不一致，任务已停止；没有使用默认提示词替代。')
        command = [str(PYTHON), "-u", str(ROOT / "deployment/launch_h3.py"), "--process", str(folder / "settings.json"),
                   "--output-dir", str(folder / "output"), "--profile", "5", "--perc-reserved-mem-max", "0.10"]
        with (folder / "run.log").open("w", encoding="utf-8") as logfile:
            with LOCK:
                if CANCEL.is_set():
                    raise InterruptedError()
                proc = subprocess.Popen(command, cwd=ROOT, env=env, stdout=logfile, stderr=subprocess.STDOUT,
                                        creationflags=subprocess.CREATE_NO_WINDOW)
                PROCESS = proc
            while proc.poll() is None:
                if CANCEL.wait(.5):
                    stop_process(proc)
                    break
            code = proc.wait()
        if CANCEL.is_set():
            raise InterruptedError()
        media = sorted((folder / "output").glob("*"), key=lambda p: p.stat().st_mtime)
        media = [p for p in media if p.suffix.lower() in {".mp4", ".jpg", ".jpeg", ".png", ".webp"}]
        if code != 0 or not media:
            tail = log_tail(job_id, 3500)
            errors = re.findall(r"(?:\[ERROR\]|RuntimeError:|ValueError:|Exception:)\s*(.+)", tail)
            raise RuntimeError(errors[-1][:400] if errors else "生成未完成，可展开运行日志查看原因。")
        result = media[-1]
        job.update(status="completed", stage="生成完成", progress=1, result=result.name)
    except InterruptedError:
        job.update(status="cancelled", stage="已停止，显卡已释放")
    except Exception as exc:
        job.update(status="failed", stage="生成失败", error=str(exc)[:600])
    finally:
        stop_process(proc)
        with LOCK:
            job["finished"] = time.time()
            save(job)
            PROCESS = None
            ACTIVE = None
            if job['status'] in {'failed', 'cancelled'}:
                try:
                    discard_job_media(DATA, job, '生成失败或已手动停止')
                except OSError:
                    pass


@asynccontextmanager
async def lifespan(app):
    with server_lock(ROOT):
        restore_jobs()
        CLEANER_STOP.clear()
        cleaner = threading.Thread(target=cleanup_loop, daemon=True)
        cleaner.start()
        try:
            yield
        finally:
            CLEANER_STOP.set()
            cleaner.join(timeout=4)
            CANCEL.set()
            stop_process(PROCESS)
            if WORKER:
                WORKER.join(timeout=15)
            with LOCK:
                cleanup_temporary_jobs('程序退出')


app = FastAPI(lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])


@app.middleware("http")
async def local_requests(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin not in {"http://127.0.0.1:7860", "http://localhost:7860"}:
        return JSONResponse({"detail": "只接受本机界面的操作。"}, status_code=403)
    response = await call_next(request)
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.get("/")
def homepage():
    return FileResponse(HERE / "studio.html", headers={"Cache-Control": "no-store"})


@app.get("/api/health")
def health():
    return {"app": "h3-direct", "ready": True}


@app.get("/api/state")
def state(client_id: str = ''):
    with LOCK:
        return {"active": ACTIVE, "advanced": advanced_process() is not None,
                "jobs": [public_job(j) for j in sorted(JOBS.values(), key=lambda j: j["created"], reverse=True)][:30]}


class ClientClose(BaseModel):
    client_id: str = Field(pattern=r'^[0-9a-f]{32}$')


@app.post('/api/session/close')
def close_client(req: ClientClose):
    # Compatibility with already-open older pages: never cancel from pagehide.
    return {'ok': True, 'generation_continues': True}


@app.post("/api/plan")
def plan(req: Generation):
    try:
        return build_plan(req)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.post("/api/jobs")
def generate(req: Generation):
    global ACTIVE, WORKER
    if req.seed is None:
        req.seed = secrets.randbelow(2147483647)
    try:
        planned = build_plan(req)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    with LOCK:
        if ACTIVE:
            raise HTTPException(409, "已有一个任务在生成，请先完成或停止它。")
        # Avoid running the separate advanced UI's model at the same time.
        try:
            import httpx
            if httpx.get("http://127.0.0.1:7862/config", trust_env=False, timeout=.5).status_code == 200:
                raise HTTPException(409, "专业界面仍在运行。先关闭专业界面后台，再使用简洁版，避免两套任务抢占显存。")
        except HTTPException:
            raise
        except Exception:
            pass
        cleanup_temporary_jobs()
        job_id = secrets.token_hex(12)
        folder = job_path(job_id)
        folder.mkdir()
        (folder / "output").mkdir()
        (folder / "settings.json").write_text(json.dumps(planned["settings"], ensure_ascii=False, indent=2), encoding="utf-8")
        job = {"id": job_id, "created": time.time(), "status": "running", "stage": "准备生成",
               "request": req.model_dump(), "plan": {k: v for k, v in planned.items() if k != "settings"},
               "seed": req.seed, "progress": 0, "saved": False}
        JOBS[job_id] = job
        ACTIVE = job_id
        CANCEL.clear()
        save(job)
        WORKER = threading.Thread(target=run_generation, args=(job_id,), daemon=True)
        WORKER.start()
        return public_job(job)


def cancel_active(job_id):
    with LOCK:
        if job_id not in JOBS:
            raise HTTPException(404, "任务不存在。")
        if ACTIVE != job_id:
            return
        JOBS[job_id].update(status="cancelling", stage="正在停止")
        CANCEL.set()
        proc, worker = PROCESS, WORKER
    stop_process(proc)
    if worker:
        worker.join(timeout=15)
        if worker.is_alive():
            raise HTTPException(409, "正在释放显卡，请稍后重试。")


@app.post("/api/jobs/{job_id}/cancel")
def cancel(job_id: str):
    job_path(job_id)
    cancel_active(job_id)
    return {"ok": True}


@app.delete("/api/jobs/{job_id}")
def delete(job_id: str):
    path = job_path(job_id)
    cancel_active(job_id)
    with LOCK:
        job = JOBS.get(job_id)
        if job and job.get('saved') is False:
            discard_job_media(DATA, job, '用户删除预览')
            return {"ok": True}
        if path.exists():
            remove_job_directory(DATA, job_id)
        JOBS.pop(job_id, None)
    return {"ok": True}


@app.post('/api/jobs/{job_id}/save')
def keep_video(job_id: str):
    folder = job_path(job_id) / 'output'
    with LOCK:
        job = JOBS.get(job_id)
        if not job or job['status'] != 'completed' or not job.get('result'):
            raise HTTPException(409, '视频尚未完成或已经清理。')
        path = (folder / job['result']).resolve()
        if path.parent != folder.resolve() or not path.is_file():
            raise HTTPException(404, '视频文件不存在。')
        kept = dict(job, saved=True)
        save(kept)  # Persist the user's decision before acknowledging it.
        job.update(kept)
        return public_job(job)


@app.get("/api/jobs/{job_id}/log")
def logs(job_id: str):
    job_path(job_id)
    if job_id not in JOBS:
        raise HTTPException(410, '该任务的记录和日志已被旧版清理，无法恢复。新版会保留最近 30 条诊断记录。')
    return {"text": log_tail(job_id, 6500)}


@app.get("/api/jobs/{job_id}/media")
def media(job_id: str, download: bool = False):
    folder = job_path(job_id) / "output"
    with LOCK:
        job = JOBS.get(job_id, {})
        name = job.get("result")
        if download and job.get('saved') is False:
            raise HTTPException(409, '请先点击保存视频。')
    if not name:
        raise HTTPException(404, "这项任务还没有成片。")
    path = (folder / name).resolve()
    if path.parent != folder.resolve() or not path.is_file():
        raise HTTPException(404, "文件不存在。")
    return FileResponse(path, filename=("H3-" + job_id[:6] + path.suffix) if download else None,
                        headers={'Cache-Control': 'no-store'})


def advanced_process():
    try:
        pid = int((ROOT / "deployment/logs/advanced.pid").read_text().strip())
        proc = psutil.Process(pid)
        command = proc.cmdline()
        if Path(proc.exe()).resolve() == PYTHON.resolve() and any(Path(arg).name == "launch_h3.py" for arg in command) and "7862" in command:
            return proc
    except (OSError, ValueError, psutil.Error):
        pass
    return None


if __name__ == "__main__":
    configure_local_proxy_bypass()
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=7860)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)
