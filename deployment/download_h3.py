"""Download only the selected public H3 components, pinned and SHA256 verified."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CKPTS = ROOT / "ckpts"
REPOS = {
    "unsloth/MiniMax-H3-GGUF": "d629413c2e5b51b38c453668b75ca3b06ca92703",
    "sakamakismile/Qwen3-VL-32B-Heretic-MiniMax-H3-NVFP4": "2814607c9e6034e2cf2c76da82f996d179567551",
    "DeepBeepMeep/MiniMax-H3": "adc81ccb71352192214d83d5fafb9487e860be39",
}
SELECT = [
    ("unsloth/MiniMax-H3-GGUF", "minimax_h3_fl2va_pruned-Q4_K.gguf", "minimax_h3_fl2va_pruned-Q4_K.gguf"),
    ("sakamakismile/Qwen3-VL-32B-Heretic-MiniMax-H3-NVFP4", "qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors", "Qwen3-VL-32B-Instruct/qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors"),
]
for filename in ["minimax_h3_video_vae_fp8mix.safetensors", "MiniMax-H3-audio_vae_fp32.safetensors", "minimax_h3/minimax_h3_latent_upscaler_3d_bf16.safetensors"]:
    SELECT.append(("DeepBeepMeep/MiniMax-H3", filename, filename))
for filename in ["config.json", "tokenizer.json", "tokenizer_config.json", "preprocessor_config.json", "vocab.json"]:
    filename = "Qwen3-VL-32B-Instruct/" + filename
    SELECT.append(("DeepBeepMeep/MiniMax-H3", filename, filename))

def get_json(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)

def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def download(entry):
    target = CKPTS / entry["local"]
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.stat().st_size == entry["size"] and (not entry["sha256"] or digest(target) == entry["sha256"]):
            print("VERIFIED existing " + entry["local"], flush=True)
            return
        raise RuntimeError("Unexpected existing file; refusing to overwrite: " + str(target))
    partial = target.with_name(target.name + ".partial")
    for attempt in range(6):
        offset = partial.stat().st_size if partial.exists() else 0
        if offset == entry["size"]:
            break
        try:
            headers = {"User-Agent": "H3-local-deployment/1.0"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            request = urllib.request.Request(entry["url"], headers=headers)
            with urllib.request.urlopen(request, timeout=60) as response:
                if offset and response.status != 206:
                    raise RuntimeError("Server did not honor resume request")
                if offset and not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                    raise RuntimeError("Unexpected Content-Range")
                with partial.open("ab" if offset else "wb") as output:
                    last = time.monotonic()
                    while True:
                        block = response.read(4 * 1024 * 1024)
                        if not block:
                            break
                        output.write(block)
                        offset += len(block)
                        if time.monotonic() - last > 25:
                            print(f"DOWNLOAD {entry['local']}: {offset / 1e9:.2f}/{entry['size'] / 1e9:.2f} GB", flush=True)
                            last = time.monotonic()
            if offset != entry["size"]:
                raise RuntimeError(f"Size mismatch: {offset} != {entry['size']}")
            break
        except Exception as exc:
            print(f"RETRY {attempt+1} {entry['local']}: {type(exc).__name__}: {exc}", flush=True)
            if attempt == 5:
                raise
            time.sleep(min(5 * (attempt + 1), 30))
    if partial.stat().st_size != entry["size"]:
        raise RuntimeError("Incomplete download: " + entry["local"])
    actual = digest(partial)
    if entry["sha256"] and actual != entry["sha256"]:
        raise RuntimeError("SHA256 mismatch: " + entry["local"])
    partial.rename(target)
    print(f"VERIFIED {entry['local']} sha256={actual}", flush=True)

def main():
    metadata = {}
    for repo, revision in REPOS.items():
        data = get_json(f"https://huggingface.co/api/models/{repo}/revision/{revision}?blobs=true")
        metadata[repo] = {f["rfilename"]: f for f in data["siblings"]}
    manifest = []
    for repo, filename, local in SELECT:
        f = metadata[repo][filename]
        manifest.append({"repo": repo, "revision": REPOS[repo], "file": filename, "local": local,
                         "size": f["size"], "sha256": f.get("lfs", {}).get("sha256"),
                         "url": f"https://huggingface.co/{repo}/resolve/{REPOS[repo]}/{urllib.parse.quote(filename, safe='/')}"})
    (ROOT / "deployment").mkdir(exist_ok=True)
    (ROOT / "deployment" / "model-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Selected download total: {sum(f['size'] for f in manifest)/1e9:.2f} GB", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(download, manifest))
    print("ALL MODEL FILES VERIFIED", flush=True)

if __name__ == "__main__":
    main()
