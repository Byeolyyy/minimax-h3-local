$ErrorActionPreference = 'Stop'
$appRoot = $PSScriptRoot
$pythonPath = Join-Path $appRoot 'env_venv\Scripts\python.exe'
$cacheRoot = Join-Path (Split-Path $appRoot -Parent) 'cache'
if (-not $env:HF_HOME) { $env:HF_HOME = Join-Path $cacheRoot 'huggingface' }
if (-not $env:PIP_CACHE_DIR) { $env:PIP_CACHE_DIR = Join-Path $cacheRoot 'pip' }
if (-not $env:TORCH_HOME) { $env:TORCH_HOME = Join-Path $cacheRoot 'torch' }
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Install into a working Wan2GP directory with env_venv first.' }
$env:PYTHONUTF8 = '1'
$env:GRADIO_ANALYTICS_ENABLED = 'False'
$logRoot = Join-Path $appRoot 'deployment\logs'
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$url = 'http://127.0.0.1:7860'
function Test-H3Ready {
    $response = $null
    try {
        # PowerShell's health checks must bypass the system proxy as well.
        $request = [System.Net.HttpWebRequest]::Create("$url/api/health")
        $request.Proxy = $null
        $request.Timeout = 2000
        $request.ReadWriteTimeout = 2000
        $response = $request.GetResponse()
        $reader = [System.IO.StreamReader]::new($response.GetResponseStream())
        # Gradio contains empty JSON keys, which ConvertFrom-Json rejects on
        # some PowerShell versions. Only inspect the readiness markers here.
        try { $body = $reader.ReadToEnd() } finally { $reader.Dispose() }
        return ($response.StatusCode -eq 200 -and $body -match '"app"\s*:\s*"h3-direct"')
    } catch { return $false }
    finally { if ($null -ne $response) { $response.Close() } }
}
try {
    if (Test-H3Ready) {
        Start-Process $url
        exit 0
    }
} catch {}
$proc = Start-Process -FilePath $pythonPath -ArgumentList @('-u', 'deployment/studio_server.py', '--port', '7860') -WorkingDirectory $appRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logRoot 'studio.log') -RedirectStandardError (Join-Path $logRoot 'studio-error.log') -PassThru
Write-Host 'Starting H3 Direct. The browser will open when the local interface is ready.'
for ($i = 0; $i -lt 120; $i++) {
    Start-Sleep -Seconds 2
    if ($proc.HasExited) { throw "H3 startup failed. Check $logRoot\studio-error.log" }
    try {
        if (Test-H3Ready) {
            $proc.Id | Set-Content -LiteralPath (Join-Path $logRoot 'server.pid')
            Start-Process $url
            exit 0
        }
    } catch {}
}
Write-Host "Startup is taking longer than expected. Check $logRoot"
