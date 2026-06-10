$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = "D:\Backup\Documents\逻辑分析流程\web\cloudflared.exe"
$psi.Arguments = "tunnel --url http://localhost:5001"
$psi.WorkingDirectory = "D:\Backup\Documents\逻辑分析流程"
$psi.UseShellExecute = $false
$psi.RedirectStandardError = $true
$psi.CreateNoWindow = $true
$p = [System.Diagnostics.Process]::Start($psi)
$sr = $p.StandardError
while ($true) {
    $line = $sr.ReadLine()
    if ($line -match "trycloudflare\.com") {
        $line | Out-File "D:\Backup\Documents\逻辑分析流程\web\tunnel_url.txt" -Encoding UTF8
        break
    }
}
