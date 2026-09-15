@echo off
rem ============================================
rem   启用 Steam 叠加层(默认行为)
rem
rem   把 SteamOverlay 改回 1,重启 Steam 后生效。
rem ============================================

setlocal EnableExtensions

set "CFG=%APPDATA%\Steam\steam.cfg"
if not exist "%APPDATA%\Steam" (
    echo [enable_steam_overlay] 找不到 Steam 用户目录。
    goto :end
)

if exist "%CFG%.bak" (
    copy /Y "%CFG%.bak" "%CFG%" >nul
    del /Q "%CFG%.bak" >nul 2>&1
    echo [enable_steam_overlay] 已从备份还原原文件。
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "$cfg = '%CFG%'; $txt = '' ; if (Test-Path $cfg) { $txt = Get-Content -LiteralPath $cfg -Raw -Encoding UTF8 }; ^
         $re = '(?ms)\[Install\](.*?)(?=\n\[|\Z)'; $m = [regex]::Match($txt, $re); ^
         if (-not $m.Success) { $txt = $txt + \"`n[Install]`nSteamOverlay=1`n\" } ^
         else { $body = $m.Groups[1].Value; $kre = '(?m)^\s*SteamOverlay\s*=\s*\d+\s*$'; ^
                if ($body -match $kre) { $body = [regex]::Replace($body, $kre, 'SteamOverlay=1') } ^
                else { $sep = if ($body -match '\n$') { '' } else { \"`n\" }; $body = $body + $sep + 'SteamOverlay=1' + \"`n\" }; ^
                $txt = $txt.Substring(0, $m.Index) + '[Install]' + \"`n\" + $body + $txt.Substring($m.Index + $m.Length) }; ^
         Set-Content -LiteralPath $cfg -Value $txt -Encoding UTF8"
    echo [enable_steam_overlay] 已写入 SteamOverlay=1。请重启 Steam。
)

:end
endlocal
pause