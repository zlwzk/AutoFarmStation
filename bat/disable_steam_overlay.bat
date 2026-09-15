@echo off
rem ============================================
rem   禁用 Steam 叠加层(Shift+Tab)
rem
rem   效果:重启 Steam 后,新启动的游戏不会再注入 GameOverlayRenderer.dll,
rem        Shift+Tab 不再唤起叠加层。已运行的进程不受影响。
rem
rem   副作用:Steam 内的截图、好友消息、网页浏览器等叠加功能一并关闭。
rem   还原:运行「启用 Steam 叠加层」脚本,或在软件 设置 → Steam 中关闭本项。
rem ============================================

setlocal EnableExtensions

set "CFG=%APPDATA%\Steam\steam.cfg"
if not exist "%APPDATA%\Steam" (
    echo [disable_steam_overlay] 找不到 Steam 用户目录,请确认已安装 Steam。
    goto :end
)

echo [disable_steam_overlay] 即将把 SteamOverlay=0 写入:
echo   %CFG%
echo.

rem 备份原文件
if exist "%CFG%" (
    copy /Y "%CFG%" "%CFG%.bak" >nul
    echo [disable_steam_overlay] 已备份到 %CFG%.bak
) else (
    echo [disable_steam_overlay] 原文件不存在,稍后会新建。
)

rem 用 PowerShell 安全改 ini:定位 [Install] 节下的 SteamOverlay 一行,没有则追加
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$cfg = '%CFG%'; $txt = '' ; if (Test-Path $cfg) { $txt = Get-Content -LiteralPath $cfg -Raw -Encoding UTF8 }; ^
     $re = '(?ms)\[Install\](.*?)(?=\n\[|\Z)'; $m = [regex]::Match($txt, $re); ^
     if (-not $m.Success) { $txt = $txt + \"`n[Install]`nSteamOverlay=0`n\" } ^
     else { $body = $m.Groups[1].Value; $kre = '(?m)^\s*SteamOverlay\s*=\s*\d+\s*$'; ^
            if ($body -match $kre) { $body = [regex]::Replace($body, $kre, 'SteamOverlay=0') } ^
            else { $sep = if ($body -match '\n$') { '' } else { \"`n\" }; $body = $body + $sep + 'SteamOverlay=0' + \"`n\" }; ^
            $txt = $txt.Substring(0, $m.Index) + '[Install]' + \"`n\" + $body + $txt.Substring($m.Index + $m.Length) }; ^
     Set-Content -LiteralPath $cfg -Value $txt -Encoding UTF8"

echo.
echo [disable_steam_overlay] 已写入。请重启 Steam 后生效。

:end
endlocal
pause