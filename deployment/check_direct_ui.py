"""Check the local page in a disposable headless Edge session."""
import base64
import json
from pathlib import Path
import subprocess
import time

import httpx
import psutil
import websocket

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'deployment/direct-ui-check'
OUT.mkdir(exist_ok=True)
edge = Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
proc = subprocess.Popen([str(edge), '--headless', '--disable-gpu', '--no-first-run',
                         '--no-default-browser-check', '--remote-debugging-port=9227',
                         '--remote-allow-origins=http://localhost:9227',
                         f'--user-data-dir={OUT / "profile"}', 'about:blank'],
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
ws = None
try:
    client = httpx.Client(trust_env=False, timeout=3)
    for _ in range(60):
        try:
            pages = client.get('http://127.0.0.1:9227/json').json()
            target = next(p for p in pages if p['type'] == 'page')
            break
        except Exception:
            time.sleep(.5)
    else:
        raise RuntimeError('Headless Edge did not start')
    ws = websocket.create_connection(target['webSocketDebuggerUrl'], timeout=15,
                                     origin='http://localhost:9227', http_no_proxy=['127.0.0.1'])
    sequence = 0
    errors = []
    def call(method, params=None):
        global sequence
        sequence += 1
        ws.send(json.dumps({'id': sequence, 'method': method, 'params': params or {}}))
        while True:
            response = json.loads(ws.recv())
            if response.get('method') == 'Runtime.exceptionThrown':
                errors.append(response['params'])
            if response.get('id') == sequence:
                if 'error' in response:
                    raise RuntimeError(response['error'])
                return response.get('result', {})
    def evaluate(expression):
        result = call('Runtime.evaluate', {'expression': expression, 'returnByValue': True, 'awaitPromise': True})
        if 'exceptionDetails' in result:
            raise RuntimeError(result['exceptionDetails'])
        return result['result'].get('value')
    call('Runtime.enable')
    call('Page.enable')
    call('Emulation.setDeviceMetricsOverride', {'width': 1280, 'height': 1100, 'deviceScaleFactor': 1, 'mobile': False})
    call('Page.navigate', {'url': 'http://127.0.0.1:7860'})
    for _ in range(30):
        if evaluate("document.getElementById('connection')?.textContent === '本地服务已连接'"):
            break
        time.sleep(.5)
    else:
        raise RuntimeError('UI did not connect')
    assert evaluate("document.querySelectorAll('textarea').length") == 1
    assert evaluate("document.getElementById('seconds').value") == '5'
    evaluate("document.getElementById('seconds').value='60'; updateDuration()")
    assert '59.71' in evaluate("document.getElementById('duration-hint').textContent")
    assert evaluate("document.getElementById('seconds').checkValidity()")
    assert evaluate("document.getElementById('resolution').value") == '832x480'
    for preset, resolution, steps in [('draft', '640x384', '8'), ('balanced', '640x384', '12'), ('quality', '832x480', '20')]:
        evaluate(f"document.getElementById('quality').value='{preset}';document.getElementById('quality').onchange()")
        assert evaluate("document.getElementById('resolution').value") == resolution
        assert evaluate("document.getElementById('steps').value") == steps
    evaluate("document.getElementById('steps').value='25';document.getElementById('steps').oninput()")
    assert evaluate("document.getElementById('quality').value") == 'custom'
    evaluate("document.getElementById('quality').value='quality';document.getElementById('quality').onchange()")
    assert not evaluate("/自动分镜|手动分镜|专业界面/.test(document.body.innerText)")
    assert evaluate("document.documentElement.scrollWidth <= innerWidth")
    print(evaluate((ROOT / 'deployment/check_job_selection.js').read_text(encoding='utf-8')))
    if evaluate("!!document.querySelector('video')"):
        for _ in range(30):
            if evaluate("document.querySelector('video').readyState >= 2"):
                break
            time.sleep(.5)
        else:
            raise RuntimeError('Generated video did not become playable')
        assert evaluate("document.querySelector('video').videoWidth > 0")
        assert evaluate("!document.getElementById('download').hidden")
        # Check the save button using the existing preview and a mocked response;
        # do not change retention of the user's real files or run the model.
        assert evaluate("""(async()=>{
          const realApi=api, previous=state.jobs.map(j=>({...j}));
          const job=state.jobs.find(j=>j.id===selected);
          if(!job)return false;
          const temporary={...job,saved:false};
          state.jobs=state.jobs.map(j=>j.id===selected?temporary:j);render();
          const before=!$('save-video').hidden&&$('download').hidden&&!$('discard').hidden;
          api=async(path,method,body)=>path.endsWith('/save')?{...temporary,saved:true}:realApi(path,method,body);
          try{await $('save-video').onclick();return before&&$('save-video').hidden&&!$('download').hidden&&$('discard').hidden;}
          finally{api=realApi;state.jobs=previous;render();}
        })()""")
    (OUT / 'desktop.png').write_bytes(base64.b64decode(call('Page.captureScreenshot', {'format': 'png'})['data']))
    call('Emulation.setDeviceMetricsOverride', {'width': 390, 'height': 844, 'deviceScaleFactor': 1, 'mobile': True})
    evaluate("document.querySelector('form details').open=true")
    assert evaluate("document.documentElement.scrollWidth <= innerWidth")
    (OUT / 'mobile.png').write_bytes(base64.b64decode(call('Page.captureScreenshot', {'format': 'png'})['data']))
    assert not errors, errors
    print('Desktop/mobile page, API connection, defaults, overflow and JavaScript: PASS')
finally:
    if ws:
        ws.close()
    try:
        parent = psutil.Process(proc.pid)
        owned = parent.children(recursive=True) + [parent]
        for child in reversed(owned):
            try:
                child.terminate()
            except psutil.Error:
                pass
    except psutil.Error:
        pass
