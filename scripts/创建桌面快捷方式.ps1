# 在桌面创建 LOFTER 下载器快捷方式（Windows）
# 快捷方式直接指向 pythonw.exe，双击即弹出 App 窗口，无控制台黑窗
# 用法: powershell -ExecutionPolicy Bypass -File scripts/创建桌面快捷方式.ps1

$root = Split-Path -Parent $PSScriptRoot
$desktop = [Environment]::GetFolderPath('Desktop')

$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path $desktop 'LOFTER 下载器.lnk'))

$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
if (Test-Path $pythonw) {
    $lnk.TargetPath = $pythonw
    $lnk.Arguments = 'app.py'
} else {
    # 无 venv 时回退到 bat 启动脚本
    $lnk.TargetPath = Join-Path $root '启动 LOFTER 下载器.bat'
}
$lnk.WorkingDirectory = $root
$lnk.IconLocation = Join-Path $root 'assets\icon.ico'
$lnk.Description = '一键启动 LOFTER 文章下载器'
$lnk.Save()

Write-Host "已创建桌面快捷方式: $(Join-Path $desktop 'LOFTER 下载器.lnk')"
