"""Install this small overlay into an existing Windows Wan2GP installation."""
from __future__ import annotations
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import sys

SOURCE = Path(__file__).resolve().parent
sys.path.insert(0, str(SOURCE / 'deployment'))
from patch_heretic import patched_source
from temporary_outputs import server_lock

FILES = [
    'Start-H3.ps1', 'Start-H3.bat', 'Stop-H3.ps1', 'Stop-H3.bat',
    'deployment/studio_server.py', 'deployment/studio.html',
    'deployment/temporary_outputs.py', 'deployment/launch_h3.py',
    'deployment/patch_heretic.py', 'deployment/download_h3.py',
    'deployment/model-manifest.json', 'deployment/test_direct.py',
    'deployment/check_direct_ui.py', 'deployment/check_job_selection.js',
]


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}


def model_definition():
    manifest = read_json(SOURCE / 'deployment/model-manifest.json')
    def url(filename):
        return next(item['url'] for item in manifest if item['file'].endswith(filename))
    return {
        'model': {
            'name': 'H3 Local - Heretic / Q4 (12GB GPU)',
            'architecture': 'minimax_h3_fl2va_pruned',
            'description': 'Local H3 Q4 with a community Heretic NVFP4 text encoder.',
            'URLs': [url('minimax_h3_fl2va_pruned-Q4_K.gguf')],
            'text_encoder_URLs': [url('qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors')],
            'system_configs': {}, 'system_configs2': {}, 'system_configs3': {},
            'video_vae_file': 'minimax_h3_video_vae_fp8mix.safetensors',
            'qkv_splitting': False, 'auto_quantize': False,
            'frame_scheduler_output_policy': 'exact',
        },
        'prompt': '', 'resolution': '832x480', 'video_length': 124,
        'num_inference_steps': 20, 'sample_solver': 'euler', 'guidance_scale': 1,
        'seed': -1, 'batch_size': 1, 'image_mode': 0, 'multi_prompts_gen_type': 'FG',
        'prompt_enhancer': '',
    }


def make_payload(root):
    if root == SOURCE:
        raise ValueError('Choose the Wan2GP installation, not this adapter repository')
    for required in ('wgp.py', 'env_venv/Scripts/python.exe', 'models/minimax_h3/minimax_h3_main.py'):
        if not (root / required).is_file():
            raise ValueError('Missing Wan2GP prerequisite: ' + required)
    source = (root / 'models/minimax_h3/minimax_h3_main.py').read_text(encoding='utf-8')
    encoder = patched_source(source)
    config = read_json(root / 'wgp_config.json')
    config.update({
        'last_model_type': 'h3_local_heretic', 'video_profile': 5, 'image_profile': 5,
        'attention_mode': 'sage2', 'compile': '', 'transformer_quantization': 'int8',
        'text_encoder_quantization': 'int8', 'smart_memory_pinning': True,
        'perc_reserved_mem_max': 10, 'multi_prompts_gen_type': 'FG',
        'enhancer_enabled': 0, 'enabled_plugins': [], 'preload_model_policy': [],
        'keep_intermediate_sliding_windows': 0,
    })
    payload = {name: (SOURCE / name).read_bytes() for name in FILES}
    payload['models/minimax_h3/minimax_h3_main.py'] = encoder.encode('utf-8')
    payload['wgp_config.json'] = (json.dumps(config, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    payload['finetunes/h3_local_heretic.json'] = (json.dumps(model_definition(), ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    payload['deployment/H3-Local-README.md'] = (SOURCE / 'README.md').read_bytes()
    payload['Start-H3-Advanced.ps1'] = b'& (Join-Path $PSScriptRoot "Start-H3.ps1")\r\n'
    payload['Start-H3-Advanced.bat'] = b'@echo off\r\ncall "%~dp0Start-H3.bat"\r\n'
    for name in payload:
        if not (root / name).resolve().is_relative_to(root):
            raise ValueError('Refusing to install outside the selected directory: ' + name)
    return payload


def install(root, check=False):
    root = Path(os.path.abspath(root)).resolve()
    payload = make_payload(root)
    if check:
        print(f'Preflight OK: {len(payload)} files; no files changed.')
        return None
    with server_lock(root):
        payload = make_payload(root)
        backup = root / 'deployment/backups' / datetime.now().strftime('h3-local-%Y%m%d-%H%M%S-%f')
        backup.mkdir(parents=True)
        originals = {}
        for name in payload:
            path = root / name
            originals[name] = path.read_bytes() if path.exists() else None
            if path.exists():
                target = backup / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
        written = []
        try:
            for name, content in payload.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                written.append(name)
                path.write_bytes(content)
        except Exception:
            for name in reversed(written):
                path = root / name
                if originals[name] is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(originals[name])
            raise
        (backup / 'installation.json').write_text(json.dumps({'files': list(payload)}, indent=2), encoding='utf-8')
    print(f'Installed in {root}\nBackup: {backup}\nStart with Start-H3.bat in the Wan2GP directory.')
    return backup


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wan2gp', type=Path, required=True)
    parser.add_argument('--check', action='store_true', help='Validate target without writing files')
    args = parser.parse_args()
    try:
        install(args.wan2gp, args.check)
    except (OSError, ValueError) as exc:
        raise SystemExit(f'Installation stopped: {exc}\nClose the H3 server before installing.')
