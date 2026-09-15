@echo off
rem ============================================
rem   [模板] 一键安装 mod
rem
rem   把这个脚本拷一份、改名为「游戏名_mod_install.bat」,
rem   然后在下面的 :install 段里写你的命令。
rem
rem   双击运行或在 AutoFarmStation 的「Bat 脚本库」里运行都行。
rem   通过参数传入:
rem     --source=...   mod 源路径
rem     --target=...   游戏安装路径
rem ============================================

setlocal EnableExtensions

set "SOURCE="
set "TARGET="

:parse_args
if "%~1"=="" goto :parse_done
if /i "%~1"=="--source" set "SOURCE=%~2" & shift & shift & goto :parse_args
if /i "%~1"=="--target" set "TARGET=%~2" & shift & shift & goto :parse_args
shift
goto :parse_args

:parse_done
if "%SOURCE%"=="" set "SOURCE=%~dp0mod"
if "%TARGET%"=="" (
    echo [mod_install_template] 没有传入 --target,也不知道默认游戏目录。
    echo   用法:%~nx0 --source="C:\path\to\mod" --target="C:\path\to\game"
    goto :end
)

echo [mod_install_template] source=%SOURCE%
echo [mod_install_template] target=%TARGET%
echo.

call :install

echo.
echo [mod_install_template] 完成。
goto :end

rem ============================================
rem   在这里写实际安装逻辑 ↓↓↓
rem ============================================
:install
rem 示例:把 mod 目录所有文件拷到游戏目录
rem xcopy /E /I /Y "%SOURCE%\*" "%TARGET%\"

rem 示例:解压 zip
rem powershell -NoProfile -Command "Expand-Archive -LiteralPath '%SOURCE%\mod.zip' -DestinationPath '%TARGET%\mods' -Force"

echo     [TODO] 请在 :install 段写你的安装命令
goto :eof

:end
endlocal
pause