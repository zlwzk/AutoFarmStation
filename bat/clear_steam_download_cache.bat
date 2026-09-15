@echo off
rem ============================================
rem   清空 Steam 下载缓存
rem
rem   场景:Steam 下载一直 0% / 卡在「正在更新」时,清理 depotcache 可解。
rem   注意:不会删除已下载好的游戏;会清掉浏览器临时文件(网页登录态可能失效)。
rem ============================================

setlocal EnableExtensions

echo [clear_steam_download_cache] 准备清理 Steam 缓存。
echo     这一步会停掉 Steam(若在运行)、清空 depotcache。
echo     需要管理员权限(脚本内已请求)。
echo.

>nul net session 2>&1
if errorlevel 1 (
    echo 正在请求管理员权限...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo 结束 Steam 进程...
taskkill /IM steam.exe /T >nul 2>&1
timeout /t 2 /nobreak >nul

set "DEPOT=%PROGRAMFILES(x86)%\Steam\depotcache"
set "HTMLCACHE=%LOCALAPPDATA%\Steam\htmlcache"

if exist "%DEPOT%" (
    echo 删除 %DEPOT% ...
    del /S /Q "%DEPOT%\*" >nul 2>&1
    echo     完成
) else (
    echo     未找到 depotcache
)

if exist "%HTMLCACHE%" (
    echo 删除 %HTMLCACHE% ...
    del /S /Q "%HTMLCACHE%\*" >nul 2>&1
    echo     完成
) else (
    echo     未找到 htmlcache
)

echo.
echo [clear_steam_download_cache] 清理完毕,可以重启 Steam 了。
pause