$ErrorActionPreference = 'Stop'
$appRoot = $PSScriptRoot
$expectedPython = Join-Path $appRoot 'env_venv\Scripts\python.exe'
foreach ($pidName in @('server.pid', 'advanced.pid')) {
    $pidFile = Join-Path $appRoot "deployment\logs\$pidName"
    if (-not (Test-Path -LiteralPath $pidFile)) { continue }
    $serverPid = [int](Get-Content -LiteralPath $pidFile)
    $serverProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $serverPid"
    if ($null -eq $serverProcess) { continue }
    if ($serverProcess.ExecutablePath -ine $expectedPython -or $serverProcess.CommandLine -notmatch '(?:wgp|launch_h3|studio_server)\.py') {
        Write-Warning 'Recorded PID belongs to a different process; it was not stopped.'
        continue
    }
    $allProcesses = @(Get-CimInstance Win32_Process)
    $ownedIds = [System.Collections.Generic.List[int]]::new()
    $ownedIds.Add($serverPid)
    for ($i = 0; $i -lt $ownedIds.Count; $i++) {
        foreach ($childProcess in $allProcesses) {
            if ($childProcess.ParentProcessId -eq $ownedIds[$i] -and -not $ownedIds.Contains([int]$childProcess.ProcessId)) {
                $ownedIds.Add([int]$childProcess.ProcessId)
            }
        }
    }
    for ($i = $ownedIds.Count - 1; $i -ge 0; $i--) {
        Stop-Process -Id $ownedIds[$i] -ErrorAction SilentlyContinue
        Wait-Process -Id $ownedIds[$i] -Timeout 10 -ErrorAction SilentlyContinue
    }
}
& $expectedPython (Join-Path $appRoot 'deployment\temporary_outputs.py')
Write-Host 'H3 stopped. GPU and RAM will be released.'
