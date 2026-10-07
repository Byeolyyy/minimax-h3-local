"""Start H3 with loopback requests bypassing proxies, preserving external proxies."""
import os
from pathlib import Path
import runpy
import sys
import urllib.request


def configure_local_proxy_bypass():
    # Capture registry-derived Windows proxies before setting NO_PROXY; otherwise
    # urllib's environment-first lookup would stop consulting the registry.
    proxies = urllib.request.getproxies()
    for scheme in ("http", "https", "all"):
        if proxies.get(scheme):
            os.environ.setdefault(scheme.upper() + "_PROXY", proxies[scheme])
    bypass = [item.strip() for item in proxies.get("no", "").split(",") if item.strip()]
    for host in ("localhost", "127.0.0.1", "::1"):
        if host not in bypass:
            bypass.append(host)
    os.environ["NO_PROXY"] = ",".join(bypass)


if __name__ == "__main__":
    configure_local_proxy_bypass()
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0, str(root))
    sys.argv[0] = str(root / "wgp.py")
    runpy.run_path(sys.argv[0], run_name="__main__")
