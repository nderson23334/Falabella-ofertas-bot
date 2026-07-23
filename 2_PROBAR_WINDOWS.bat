@echo off
chcp 65001 >nul
title Probar Falabella Bot V2 seguro
cd /d "%~dp0"

if not exist ".env" (
  echo Falta .env. Ejecuta primero 1_CONFIGURAR_WINDOWS.bat
  pause
  exit /b 1
)

where py >nul 2>nul
if %errorlevel%==0 (
  set "PYTHON_CMD=py"
) else (
  set "PYTHON_CMD=python"
)

if not exist ".venv\Scripts\python.exe" (
  echo Creando entorno privado...
  %PYTHON_CMD% -m venv .venv
  if errorlevel 1 goto :error
)

echo Instalando o comprobando librerias...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo Comprobando Chromium...
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto :error

echo.
echo Revisando Falabella. Puede tardar varios minutos...
".venv\Scripts\python.exe" run_once.py
if errorlevel 1 goto :error

echo.
echo PRUEBA TERMINADA CORRECTAMENTE.
pause
exit /b 0

:error
echo.
echo OCURRIO UN ERROR.
echo La version segura nunca muestra el token en esta ventana.
echo Toma una foto completa del error.
pause
exit /b 1
