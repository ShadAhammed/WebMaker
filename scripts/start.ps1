# WebMaker - Start local development environment
# Usage:  .\scripts\start.ps1
# Stop:   Ctrl+C (stops PHP; MariaDB keeps running)

$Root      = Split-Path $PSScriptRoot -Parent
$PhpExe    = "E:\\AI-Optimizer\\WebMaker\\bin\\php\\php.exe"
$PhpIni    = "E:\\AI-Optimizer\\WebMaker\\bin\\php\\php.ini"
$MysqldExe = "E:\\AI-Optimizer\\WebMaker\\db\\mariadb\\bin\\mariadbd.exe"
$MyIni     = "E:\\AI-Optimizer\\WebMaker\\db\\my.ini"
$WpDir     = Join-Path $Root "wordpress"
$Router    = Join-Path $Root "scripts\router.php"
$DB_PORT   = 3307
$WP_PORT   = 8080

function Wait-TCP {
    param([int]$Port, [int]$Sec = 20)
    $end = (Get-Date).AddSeconds($Sec)
    while ((Get-Date) -lt $end) {
        try {
            $c = New-Object System.Net.Sockets.TcpClient
            $c.Connect("127.0.0.1", $Port)
            $c.Close()
            return $true
        } catch { Start-Sleep -Milliseconds 400 }
    }
    return $false
}

Write-Host ""
Write-Host "  WebMaker Dev Environment" -ForegroundColor Magenta
Write-Host ""

# Start MariaDB if not already running
if (Wait-TCP $DB_PORT 2) {
    Write-Host "  [OK] MariaDB already running on port $DB_PORT" -ForegroundColor Green
} else {
    Write-Host "  Starting MariaDB on port $DB_PORT..." -ForegroundColor Yellow
    Start-Process -FilePath $MysqldExe -ArgumentList ("--defaults-file=`"" + $MyIni + "`"") -NoNewWindow
    if (Wait-TCP $DB_PORT 30) {
        Write-Host "  [OK] MariaDB started" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] MariaDB failed to start" -ForegroundColor Red; exit 1
    }
}

Write-Host ""
Write-Host "  WordPress : http://localhost:$WP_PORT" -ForegroundColor Cyan
Write-Host "  Admin     : http://localhost:$WP_PORT/wp-admin" -ForegroundColor Cyan
Write-Host "  Username  : admin" -ForegroundColor Cyan
Write-Host "  Password  : webmaker_admin_2026" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Press Ctrl+C to stop the web server." -ForegroundColor Gray
Write-Host ""

# Start PHP built-in server (use -c to bypass any system PHP registry)
# Bind to 127.0.0.1 so IPv4 health checks (verify.py) can reach the server on Windows
& $PhpExe -c $PhpIni -S ("127.0.0.1:" + $WP_PORT) -t $WpDir $Router