@echo off
chcp 65001 >nul
title Actualizar token seguro
cd /d "%~dp0"

echo ============================================================
echo   ACTUALIZAR TOKEN DEL BOT
echo ============================================================
echo.
echo Primero revoca el token anterior desde BotFather.
echo No compartas el token nuevo ni tomes fotos mientras este visible.
echo.

set /p "BOT_TOKEN=Pega el token NUEVO y presiona ENTER: "
if "%BOT_TOKEN%"=="" (
  echo Falta el token.
  pause
  exit /b 1
)

set /p "CHAT_ID=Pega nuevamente tu chat ID y presiona ENTER: "
echo %CHAT_ID%| findstr /r /x /c:"-*[0-9][0-9]*" >nul
if errorlevel 1 (
  echo El chat ID no parece valido.
  pause
  exit /b 1
)

(
echo TELEGRAM_BOT_TOKEN=%BOT_TOKEN%
echo TELEGRAM_CHAT_ID=%CHAT_ID%
echo LOG_LEVEL=INFO
) > ".env"

echo.
echo TOKEN ACTUALIZADO CORRECTAMENTE.
echo Ahora ejecuta 2_PROBAR_WINDOWS.bat
pause
