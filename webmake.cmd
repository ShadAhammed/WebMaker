@echo off
REM webmake — launch WebMaker Tk UI + open demo site in browser
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0webmake.ps1" %*
