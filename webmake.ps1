# webmake — launch the WebMaker Tk desk app + open the WordPress demo
# Usage (from WebMaker folder):
#   .\webmake
#   .\webmake.ps1
#   webmake.cmd
#   python run.py webmake

$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
Set-Location $Root

$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
    Write-Host "Virtual environment not found. Run: python setup\setup.py" -ForegroundColor Red
    exit 1
}

$DbPort = 3307
$WpPort = 8080
$DemoUrl = "http://127.0.0.1:$WpPort"

function Test-Port([int]$Port) {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $c.Connect("127.0.0.1", $Port)
        $c.Close()
        return $true
    } catch {
        return $false
    }
}

# MariaDB
if (-not (Test-Port $DbPort)) {
    $mysqld = Join-Path $Root "db\mariadb\bin\mariadbd.exe"
    if (-not (Test-Path $mysqld)) {
        $mysqld = Join-Path $Root "db\mariadb\bin\mysqld.exe"
    }
    $myIni = Join-Path $Root "db\my.ini"
    if ((Test-Path $mysqld) -and (Test-Path $myIni)) {
        Write-Host "Starting MariaDB on port $DbPort..." -ForegroundColor Yellow
        Start-Process -FilePath $mysqld -ArgumentList "--defaults-file=`"$myIni`"" -WindowStyle Hidden
        $ok = $false
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep -Seconds 1
            if (Test-Port $DbPort) { $ok = $true; break }
        }
        if ($ok) { Write-Host "[OK] MariaDB running" -ForegroundColor Green }
        else { Write-Host "[WARN] MariaDB did not start in time" -ForegroundColor Yellow }
    } else {
        Write-Host "[WARN] MariaDB binaries not found — demo site may be unavailable" -ForegroundColor Yellow
    }
} else {
    Write-Host "[OK] MariaDB already on $DbPort" -ForegroundColor Green
}

# PHP built-in server
if (-not (Test-Port $WpPort)) {
    $php = Join-Path $Root "bin\php\php.exe"
    $ini = Join-Path $Root "bin\php\php.ini"
    $wp  = Join-Path $Root "wordpress"
    $router = Join-Path $Root "scripts\router.php"
    if ((Test-Path $php) -and (Test-Path $wp)) {
        Write-Host "Starting PHP server on $WpPort..." -ForegroundColor Yellow
        $args = @()
        if (Test-Path $ini) { $args += @("-c", $ini) }
        $args += @("-S", "127.0.0.1:$WpPort", "-t", $wp)
        if (Test-Path $router) { $args += $router }
        Start-Process -FilePath $php -ArgumentList $args -WindowStyle Hidden
        Start-Sleep -Seconds 2
        if (Test-Port $WpPort) { Write-Host "[OK] PHP server running" -ForegroundColor Green }
        else { Write-Host "[WARN] PHP server may not have started" -ForegroundColor Yellow }
    } else {
        Write-Host "[WARN] PHP/WordPress not found — demo browser may fail" -ForegroundColor Yellow
    }
} else {
    Write-Host "[OK] PHP server already on $WpPort" -ForegroundColor Green
}

# Demo browser is opened once by the Tk app (launch_app) — do not open here.
Write-Host "Demo URL (unchanged): $DemoUrl" -ForegroundColor Cyan
Write-Host "Demo content is preserved across webmake restarts." -ForegroundColor DarkGray

Write-Host "Starting webmake..." -ForegroundColor Cyan
& $VenvPy -c "from webmaker.ui.tk_app import main; main()"
exit $LASTEXITCODE
