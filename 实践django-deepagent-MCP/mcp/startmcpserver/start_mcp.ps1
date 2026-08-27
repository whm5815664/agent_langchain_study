# MCP 本地桥接启动脚本
# 用法: .\mcp\startmcpserver\start_mcp.ps1
# 可选: .\mcp\startmcpserver\start_mcp.ps1 --port 18765

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\..")

Write-Host "========================================"
Write-Host "  MCP Local Bridge"
Write-Host "  项目目录: $(Get-Location)"
Write-Host "  停止: Ctrl+C"
Write-Host "========================================"
Write-Host ""

python -m mcp.startmcpserver @args
