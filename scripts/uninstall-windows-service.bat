@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "SERVICE_NAME=TelegramVoiceForwarder"
set "DISPLAY_NAME=Telegram Voice Forwarder"
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROJECT_DIR=%%~fI"

cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo ERROR: Could not change to the project directory:
    echo   %PROJECT_DIR%
    exit /b 1
)

echo Uninstalling %DISPLAY_NAME%...
echo.

powershell.exe -NoProfile -Command ^
  "$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent()); if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { exit 1 }"
if not errorlevel 1 goto :elevated

if defined GSUDO_EXE goto :check_gsudo_path
where.exe gsudo.exe >nul 2>&1
if errorlevel 1 goto :gsudo_missing
set "GSUDO_EXE=gsudo.exe"
goto :launch_elevated

:check_gsudo_path
if not exist "%GSUDO_EXE%" goto :gsudo_missing

:launch_elevated
echo Administrator rights are required. Restarting through gsudo...
"%GSUDO_EXE%" --chdir "%PROJECT_DIR%" "%~f0" %*
set "GSUDO_EXIT=%ERRORLEVEL%"
if "%GSUDO_EXIT%"=="999" echo ERROR: gsudo could not elevate the uninstaller.
exit /b %GSUDO_EXIT%

:elevated
sc.exe query "%SERVICE_NAME%" >nul 2>&1
if errorlevel 1 goto :service_missing

echo Stopping service %SERVICE_NAME%...
powershell.exe -NoProfile -Command ^
  "$ErrorActionPreference = 'Stop'; $service = Get-Service -Name '%SERVICE_NAME%' -ErrorAction Stop; if ($service.Status -ne 'Stopped') { Stop-Service -Name '%SERVICE_NAME%' -ErrorAction Stop; $service.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30)) }"
if errorlevel 1 goto :stop_failed

echo Removing service %SERVICE_NAME%...
sc.exe delete "%SERVICE_NAME%" >nul
if errorlevel 1 goto :delete_failed

powershell.exe -NoProfile -Command ^
  "$deadline = [DateTime]::UtcNow.AddSeconds(30); while ($null -ne (Get-Service -Name '%SERVICE_NAME%' -ErrorAction SilentlyContinue)) { if ([DateTime]::UtcNow -ge $deadline) { exit 1 }; Start-Sleep -Milliseconds 200 }"
if errorlevel 1 goto :delete_pending

echo.
echo %DISPLAY_NAME% was stopped and removed successfully.
echo Project files and runtime data were not deleted.
exit /b 0

:service_missing
echo Service %SERVICE_NAME% is not installed. Nothing to remove.
exit /b 0

:gsudo_missing
echo ERROR: Administrator rights are required and gsudo was not found.
echo Install it with:
echo   winget install gerardog.gsudo
echo Then restart the terminal, add gsudo.exe to PATH, or set GSUDO_EXE
echo to its full path. Alternatively, run this script from an elevated terminal.
echo Documentation: https://gerardog.github.io/gsudo/docs/usage
exit /b 1

:stop_failed
echo ERROR: The service could not be stopped within 30 seconds.
echo It was not removed. Inspect it with:
echo   sc.exe query "%SERVICE_NAME%"
exit /b 1

:delete_failed
echo ERROR: Windows could not remove the service.
echo Inspect it with:
echo   sc.exe query "%SERVICE_NAME%"
exit /b 1

:delete_pending
echo ERROR: Windows accepted the removal request, but the service is still pending deletion.
echo Close services.msc and other service-management tools, then check again with:
echo   sc.exe query "%SERVICE_NAME%"
exit /b 1
