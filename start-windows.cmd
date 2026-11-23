@echo off
setlocal
pushd "%~dp0" || exit /b 1
if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info < (3, 10))" || goto failed
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt || goto failed
start "" /b powershell.exe -NoProfile -Command "$url='http://127.0.0.1:5444'; for ($i=0; $i -lt 30; $i++) { try { $api=Invoke-RestMethod ($url+'/openapi.json') -TimeoutSec 1; if ($api.paths.'/api/commissions') { Start-Process $url; break } } catch {}; Start-Sleep -Milliseconds 500 }"
".venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port 5444 --no-proxy-headers
if errorlevel 1 goto failed
popd
exit /b 0

:failed
echo Could not start CommissionManage. Check Python 3.10+, dependencies and port 5444.
pause
popd
exit /b 1
