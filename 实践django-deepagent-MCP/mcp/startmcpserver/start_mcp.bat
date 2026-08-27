@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0..\.."

echo ========================================
echo   MCP Local Bridge
echo   项目目录: %CD%
echo   停止: Ctrl+C
echo ========================================
echo.

python -m mcp.startmcpserver %*
if errorlevel 1 (
  echo.
  echo 启动失败。请确认已安装依赖，且当前 Python 可导入 mcp 包。
  pause
  exit /b 1
)
