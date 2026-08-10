#Requires -Version 5.1
<#
.SYNOPSIS
    WebMaker - Complete local development environment setup.
.DESCRIPTION
    Installs and configures PHP, MariaDB, WordPress and WP-CLI for a
    self-contained local WordPress development stack.
.EXAMPLE
    .\setup\setup.ps1
    .\setup\setup.ps1 -Force   # Reinstall everything
#>

[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$SkipPHP,
    [switch]$SkipDB,
    [switch]$SkipWordPress
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"

$Root    = Split-Path $PSScriptRoot -Parent
$BinDir  = Join-Path $Root  "bin"
$PhpDir  = Join-Path $BinDir "php"
$DbDir   = Join-Path $Root  "db"
$MdbDir  = Join-Path $DbDir  "mariadb"
$DataDir = Join-Path $DbDir  "data"
$WpDir   = Join-Path $Root  "wordpress"
$LogDir  = Join-Path $Root  "logs"
$ScriptsDir = Join-Path $Root "scripts"

# Credentials
$DB_PORT      = 3307
$DB_ROOT_PASS = "webmaker_root_2026"
$DB_NAME      = "wordpress_webmaker"
$DB_USER      = "wp_user"
$DB_PASS      = "webmaker_2026"

$WP_PORT       = 8080
$WP_ADMIN      = "admin"
$WP_ADMIN_PASS = "webmaker_admin_2026"
$WP_ADMIN_EMAIL= "admin@webmaker.local"
$WP_SITE_URL   = "http://localhost:$WP_PORT"
$WP_SITE_TITLE = "WebMaker Demo"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Write-Step { param($m) Write-Host "`n[*] $m" -ForegroundColor Cyan   }
function Write-OK   { param($m) Write-Host "  [OK]   $m" -ForegroundColor Green  }
function Write-WARN { param($m) Write-Host "  [WARN] $m" -ForegroundColor Yellow }
function Write-FAIL { param($m) Write-Host "  [FAIL] $m" -ForegroundColor Red    }

function Ensure-Dir { param($p) if (-not (Test-Path $p)) { New-Item -ItemType Directory -Force -Path $p | Out-Null } }

function Wait-Port {
    param([int]$Port, [int]$TimeoutSec = 30)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $tcp = New-Object System.Net.Sockets.TcpClient
            $tcp.Connect("127.0.0.1", $Port)
            $tcp.Close()
            return $true
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    return $false
}

# Write a file using UTF-8 without BOM (avoids Windows quirks)
function Write-UTF8 {
    param([string]$Path, [string]$Content)
    $enc = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($Path, $Content, $enc)
}

# ---------------------------------------------------------------------------
# PHP
# ---------------------------------------------------------------------------

function Configure-PhpIni {
    param([string]$Dir)
    # Always (re)configure php.ini with absolute extension_dir.
    # This overrides any system-wide PHP registry/PHPRC settings.
    $ini    = Join-Path $Dir "php.ini"
    $extDir = (Join-Path $Dir "ext").Replace('\', '/')

    # Start from php.ini-development if php.ini doesn't exist yet
    if (-not (Test-Path $ini)) {
        $devIni = Join-Path $Dir "php.ini-development"
        if (Test-Path $devIni) { Copy-Item $devIni $ini -Force }
    }

    $raw = [System.IO.File]::ReadAllText($ini)
    # Set absolute extension_dir (matches both commented and uncommented forms)
    $raw = $raw -replace '(?m)^;?\s*extension_dir\s*=.*$', ('extension_dir = "' + $extDir + '"')
    [System.IO.File]::WriteAllText($ini, $raw)

    # Enable WordPress-required extensions
    $exts = @('mysqli','pdo_mysql','gd','mbstring','openssl','curl','zip','exif','fileinfo','intl','sodium')
    foreach ($ext in $exts) {
        $raw = [System.IO.File]::ReadAllText($ini)
        $raw = $raw -replace (";extension=" + $ext + "(\s)"), ("extension=" + $ext + '$1')
        $raw = $raw -replace (";extension=" + $ext + '$'),    ("extension=" + $ext)
        [System.IO.File]::WriteAllText($ini, $raw)
    }
}

function Get-PhpIni {
    return (Join-Path $PhpDir "php.ini")
}

function Find-PHP {
    $local = Join-Path $PhpDir "php.exe"
    if (Test-Path $local) { return $local }
    try { return (Get-Command php -ErrorAction Stop).Source } catch {}
    return $null
}

function Install-PHP {
    Write-Step "Installing PHP 8.3 NTS x64 (portable)"
    Ensure-Dir $BinDir

    Write-Host "  Resolving latest PHP 8.3 NTS x64..."
    # Query the releases directory listing (more reliable than the download page)
    $dirPage = (Invoke-WebRequest "https://windows.php.net/downloads/releases/" -UseBasicParsing).Content
    $matches2 = [regex]::Matches($dirPage, 'php-8\.3\.(\d+)-nts-Win32-vs16-x64\.zip')
    if ($matches2.Count -gt 0) {
        # Pick highest patch version
        $best = $matches2 | Sort-Object { [int]([regex]::Match($_.Value, '8\.3\.(\d+)').Groups[1].Value) } | Select-Object -Last 1
        $phpFile = $best.Value
    } else {
        Write-WARN "Could not detect PHP version from releases directory - using fallback"
        $phpFile = "php-8.3.22-nts-Win32-vs16-x64.zip"
    }
    $url    = "https://windows.php.net/downloads/releases/" + $phpFile
    $tmpZip = Join-Path $env:TEMP "php83_nts.zip"
    Write-Host "  Downloading $url..."
    Invoke-WebRequest -Uri $url -OutFile $tmpZip -UseBasicParsing

    Write-Host "  Extracting to $PhpDir..."
    if (Test-Path $PhpDir) { Remove-Item $PhpDir -Recurse -Force }
    Expand-Archive -Path $tmpZip -DestinationPath $PhpDir -Force
    Remove-Item $tmpZip -Force

    Configure-PhpIni $PhpDir

    Configure-PhpIni $PhpDir
    Write-OK "PHP installed at $PhpDir"
    return (Join-Path $PhpDir "php.exe")
}

# ---------------------------------------------------------------------------
# MariaDB
# ---------------------------------------------------------------------------

function Find-MySQLd {
    # Prefer our portable install; accept both mariadbd.exe (11.x) and mysqld.exe
    foreach ($name in @('mariadbd.exe','mysqld.exe')) {
        $local = Join-Path $MdbDir "bin\$name"
        if (Test-Path $local) { return $local }
    }
    foreach ($cmd in @('mariadbd','mysqld')) {
        try { return (Get-Command $cmd -ErrorAction Stop).Source } catch {}
    }
    foreach ($pat in @(
        "C:\Program Files\MariaDB*\bin\mariadbd.exe",
        "C:\Program Files\MariaDB*\bin\mysqld.exe",
        "C:\Program Files\MySQL\MySQL Server*\bin\mysqld.exe",
        "C:\xampp\mysql\bin\mysqld.exe"
    )) {
        $hit = Get-Item $pat -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return $null
}

function Get-MariaDBDownloadUrl {
    # archive.mariadb.org is a direct download server — no auth/redirect portal
    $archiveBase = "https://archive.mariadb.org"

    # Try REST API to resolve latest 11.4.x version name
    $ver = "11.4.5"
    foreach ($candidate in @("11.4","11.4.5","11.4.4","11.4.3")) {
        try {
            $api  = Invoke-RestMethod "https://downloads.mariadb.org/rest-api/mariadb/$candidate/" -ErrorAction Stop
            $file = $api.files | Where-Object {
                $_.file_name -like "mariadb-*-winx64.zip" -and $_.file_name -notlike "*debug*"
            } | Select-Object -First 1
            if ($file) {
                $ver = [regex]::Match($file.file_name, 'mariadb-([\d.]+)-winx64').Groups[1].Value
                break
            }
        } catch {}
    }

    $zipName = "mariadb-$ver-winx64.zip"
    $url     = "$archiveBase/mariadb-$ver/winx64-packages/$zipName"
    return @($ver, $url)
}

function Test-ZipFile {
    param([string]$Path)
    # Check PK magic bytes (50 4B 03 04)
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    return ($bytes.Length -gt 4 -and $bytes[0] -eq 0x50 -and $bytes[1] -eq 0x4B)
}

function Install-MariaDB {
    Write-Step "Installing MariaDB portable (no-install zip)"
    Ensure-Dir $DbDir

    $info    = Get-MariaDBDownloadUrl
    $ver     = $info[0]
    $url     = $info[1]
    $tmpZip  = Join-Path $env:TEMP ("mariadb-" + $ver + "-winx64.zip")

    Write-Host "  MariaDB $ver -> $url"
    Invoke-WebRequest -Uri $url -OutFile $tmpZip -UseBasicParsing

    if (-not (Test-ZipFile $tmpZip)) {
        Remove-Item $tmpZip -Force -ErrorAction SilentlyContinue
        throw "Downloaded MariaDB file is not a valid zip. Check network/URL: $url"
    }

    Write-Host "  Extracting..."
    $tmpDir = Join-Path $env:TEMP "mdb_extract"
    if (Test-Path $tmpDir) { Remove-Item $tmpDir -Recurse -Force }
    Expand-Archive -Path $tmpZip -DestinationPath $tmpDir -Force
    Remove-Item $tmpZip -Force

    $extracted = Get-ChildItem $tmpDir | Select-Object -First 1
    if (Test-Path $MdbDir) { Remove-Item $MdbDir -Recurse -Force }
    Move-Item $extracted.FullName $MdbDir
    Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue

    Write-OK "MariaDB $ver installed at $MdbDir"
    return (Join-Path $MdbDir "bin\mysqld.exe")
}

function Get-MyIniPath {
    # Write my.ini (safe to call multiple times)
    $ini      = Join-Path $DbDir "my.ini"
    $dataFwd  = $DataDir.Replace('\', '/')
    $baseFwd  = $MdbDir.Replace('\', '/')

    $logFwd   = (Join-Path $LogDir "mariadb_server.log").Replace('\', '/')
    $content  = "[mysqld]`n"
    $content += "port                    = $DB_PORT`n"
    $content += "datadir                 = $dataFwd`n"
    $content += "basedir                 = $baseFwd`n"
    $content += "log-error               = $logFwd`n"
    $content += "socket                  = MYSQL`n"
    $content += "bind-address            = 127.0.0.1`n"
    $content += "character-set-server    = utf8mb4`n"
    $content += "collation-server        = utf8mb4_unicode_ci`n"
    $content += "max_allowed_packet      = 64M`n"
    $content += "innodb_buffer_pool_size = 128M`n"
    $content += "`n[client]`n"
    $content += "port = $DB_PORT`n"

    Write-UTF8 $ini $content
    return $ini
}

function Initialize-MariaDB {
    param([string]$mysqldExe)
    Write-Step "Initializing MariaDB data directory"

    $ini     = Get-MyIniPath
    $mysqlDir = Join-Path $DataDir "mysql"

    # Properly initialized = mysql/ system-table directory exists
    if (Test-Path $mysqlDir) {
        Write-OK "Data directory already properly initialized - skipping"
        return $ini
    }

    # Partial initialization (InnoDB files but no mysql/) — wipe and redo
    if (Test-Path $DataDir) {
        $files = Get-ChildItem $DataDir -ErrorAction SilentlyContinue
        if ($files) {
            Write-WARN "Partial data directory found - clearing for fresh initialization"
            Remove-Item $DataDir -Recurse -Force
        }
    }
    Ensure-Dir $DataDir

    # Prefer mysql_install_db.exe (designed for Windows data-dir init)
    $binDir      = Split-Path $mysqldExe
    $installDbExe = Join-Path $binDir "mysql_install_db.exe"
    if (-not (Test-Path $installDbExe)) {
        $installDbExe = Join-Path $binDir "mariadb-install-db.exe"
    }

    $logFile    = Join-Path $LogDir "mariadb_init.log"
    $errLogFile = Join-Path $LogDir "mariadb_init_err.log"

    if (Test-Path $installDbExe) {
        Write-Host "  Using mysql_install_db.exe for initialization..."
        # Use .NET Process for reliable stdout/stderr capture
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName  = $installDbExe
        $psi.Arguments = "--datadir=`"$DataDir`""
        $psi.UseShellExecute       = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError  = $true
        $psi.CreateNoWindow         = $true

        $p = [System.Diagnostics.Process]::Start($psi)
        $stdout = $p.StandardOutput.ReadToEnd()
        $stderr = $p.StandardError.ReadToEnd()
        $p.WaitForExit()
        ($stdout + "`n" + $stderr) | Out-File $logFile
    } else {
        Write-Host "  Using mysqld --initialize-insecure..."
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName  = $mysqldExe
        $psi.Arguments = "--defaults-file=`"$ini`" --initialize-insecure"
        $psi.UseShellExecute       = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError  = $true
        $psi.CreateNoWindow         = $true

        $p = [System.Diagnostics.Process]::Start($psi)
        $stdout = $p.StandardOutput.ReadToEnd()
        $stderr = $p.StandardError.ReadToEnd()
        $p.WaitForExit()
        ($stdout + "`n" + $stderr) | Out-File $logFile
    }

    # Verify
    if (Test-Path $mysqlDir) {
        Write-OK "Data directory initialized successfully"
    } else {
        Write-FAIL "Initialization failed - check $logFile"
        if (Test-Path $logFile) {
            Get-Content $logFile | Select-Object -Last 20 | ForEach-Object { Write-Host "    $_" }
        }
        throw "MariaDB initialization failed"
    }
    return $ini
}

function Start-MariaDB {
    param([string]$mysqldExe, [string]$myIni)

    if (Wait-Port $DB_PORT 2) {
        Write-OK "Database already reachable on port $DB_PORT"
        return
    }

    Write-Host "  Starting MariaDB daemon..."
    $logFile = Join-Path $LogDir "mariadb.log"
    Ensure-Dir $LogDir
    # Use Start-Process so mysqld runs independently; its output goes to log
    Start-Process -FilePath $mysqldExe `
        -ArgumentList ("--defaults-file=`"" + $myIni + "`"") `
        -NoNewWindow `
        -RedirectStandardOutput $logFile `
        -RedirectStandardError  (Join-Path $LogDir "mariadb_err.log")

    Write-Host "  Waiting for port $DB_PORT (up to 45 s)..."
    if (Wait-Port $DB_PORT 45) {
        Write-OK "MariaDB is up"
    } else {
        Write-FAIL "MariaDB did not start within 45 s - check $logFile"
        throw "MariaDB startup timeout"
    }
}

function Setup-Database {
    param([string]$mysqldExe)
    Write-Step "Creating WordPress database and user"

    $mysqlBin = Join-Path (Split-Path $mysqldExe) "mysql.exe"

    # Write SQL to temp files to avoid PowerShell quoting conflicts with 'user'@'host' syntax
    $sql1 = Join-Path $env:TEMP "wm_root.sql"
    $sql2 = Join-Path $env:TEMP "wm_setup.sql"

    Write-UTF8 $sql1 ("ALTER USER 'root'@'localhost' IDENTIFIED BY '" + $DB_ROOT_PASS + "';`nFLUSH PRIVILEGES;`n")
    Write-UTF8 $sql2 (
        "CREATE DATABASE IF NOT EXISTS ``" + $DB_NAME + "`` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;`n" +
        "CREATE USER IF NOT EXISTS '" + $DB_USER + "'@'localhost' IDENTIFIED BY '" + $DB_PASS + "';`n" +
        "GRANT ALL PRIVILEGES ON ``" + $DB_NAME + "``.* TO '" + $DB_USER + "'@'localhost';`n" +
        "FLUSH PRIVILEGES;`n"
    )

    $prevEA = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    # Set root password (connects without password first)
    Get-Content $sql1 | & $mysqlBin --port=$DB_PORT --user=root --connect-timeout=5 2>&1 | Out-Null

    # Create DB + user
    Get-Content $sql2 | & $mysqlBin --port=$DB_PORT --user=root ("--password=" + $DB_ROOT_PASS) --connect-timeout=5 2>&1 | Out-Null
    $ErrorActionPreference = $prevEA

    Remove-Item $sql1, $sql2 -Force -ErrorAction SilentlyContinue
    Write-OK "Database '$DB_NAME' created, user '$DB_USER' granted"
}

# ---------------------------------------------------------------------------
# WordPress
# ---------------------------------------------------------------------------

function Install-WordPress {
    Write-Step "Downloading WordPress (latest)"

    if ((Test-Path (Join-Path $WpDir "wp-includes\version.php")) -and -not $Force) {
        Write-OK "WordPress already present - skipping download"
        return
    }

    $tmpZip = Join-Path $env:TEMP "wordpress_latest.zip"
    Write-Host "  Downloading https://wordpress.org/latest.zip..."
    Invoke-WebRequest "https://wordpress.org/latest.zip" -OutFile $tmpZip -UseBasicParsing

    $tmpDir = Join-Path $env:TEMP "wp_extract"
    if (Test-Path $tmpDir) { Remove-Item $tmpDir -Recurse -Force }
    Expand-Archive -Path $tmpZip -DestinationPath $tmpDir -Force
    Remove-Item $tmpZip -Force

    if (Test-Path $WpDir) { Remove-Item $WpDir -Recurse -Force }
    Move-Item (Join-Path $tmpDir "wordpress") $WpDir
    Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue

    Ensure-Dir (Join-Path $WpDir "wp-content\uploads")
    Write-OK "WordPress installed at $WpDir"
}

function Write-WpConfig {
    Write-Step "Generating wp-config.php"

    # Fetch salts
    $salts = ""
    try {
        $salts = (Invoke-WebRequest "https://api.wordpress.org/secret-key/1.1/salt/" -UseBasicParsing).Content
        Write-OK "Security salts fetched from WordPress API"
    } catch {
        Write-WARN "Could not fetch salts - using placeholders (replace before production use)"
        $salts  = "define('AUTH_KEY',         'changeme-auth-key');" + [Environment]::NewLine
        $salts += "define('SECURE_AUTH_KEY',  'changeme-secure-auth-key');" + [Environment]::NewLine
        $salts += "define('LOGGED_IN_KEY',    'changeme-logged-in-key');" + [Environment]::NewLine
        $salts += "define('NONCE_KEY',        'changeme-nonce-key');" + [Environment]::NewLine
        $salts += "define('AUTH_SALT',        'changeme-auth-salt');" + [Environment]::NewLine
        $salts += "define('SECURE_AUTH_SALT', 'changeme-secure-auth-salt');" + [Environment]::NewLine
        $salts += "define('LOGGED_IN_SALT',   'changeme-logged-in-salt');" + [Environment]::NewLine
        $salts += "define('NONCE_SALT',       'changeme-nonce-salt');"
    }

    # Build config line by line (avoid here-string + PHP syntax conflicts)
    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.Add("<?php")
    $lines.Add("// WebMaker local WordPress configuration")
    $lines.Add("")
    $lines.Add("define( 'DB_NAME',     '" + $DB_NAME + "' );")
    $lines.Add("define( 'DB_USER',     '" + $DB_USER + "' );")
    $lines.Add("define( 'DB_PASSWORD', '" + $DB_PASS + "' );")
    $lines.Add("define( 'DB_HOST',     '127.0.0.1:" + $DB_PORT + "' );")
    $lines.Add("define( 'DB_CHARSET',  'utf8mb4' );")
    $lines.Add("define( 'DB_COLLATE',  '' );")
    $lines.Add("")
    $lines.Add($salts)
    $lines.Add("")
    $lines.Add('$table_prefix = ' + "'wp_';")
    $lines.Add("")
    $lines.Add("define( 'WP_DEBUG',         true );")
    $lines.Add("define( 'WP_DEBUG_LOG',     true );")
    $lines.Add("define( 'WP_DEBUG_DISPLAY', false );")
    $lines.Add("")
    $lines.Add("define( 'WP_SITEURL', '" + $WP_SITE_URL + "' );")
    $lines.Add("define( 'WP_HOME',    '" + $WP_SITE_URL + "' );")
    $lines.Add("")
    $lines.Add("define( 'DISALLOW_FILE_EDIT', false );")
    $lines.Add("")
    $lines.Add("if ( ! defined( 'ABSPATH' ) ) {")
    $lines.Add("    define( 'ABSPATH', __DIR__ . '/' );")
    $lines.Add("}")
    $lines.Add("require_once ABSPATH . 'wp-settings.php';")

    Write-UTF8 (Join-Path $WpDir "wp-config.php") ($lines -join [Environment]::NewLine)
    Write-OK "wp-config.php written"
}

# ---------------------------------------------------------------------------
# WP-CLI
# ---------------------------------------------------------------------------

function Install-WpCLI {
    Write-Step "Installing WP-CLI"
    Ensure-Dir $BinDir
    $phar = Join-Path $BinDir "wp-cli.phar"

    if ((Test-Path $phar) -and -not $Force) {
        Write-OK "WP-CLI already present"
        return $phar
    }

    Invoke-WebRequest `
        "https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar" `
        -OutFile $phar -UseBasicParsing
    Write-OK "WP-CLI downloaded: $phar"
    return $phar
}

function Complete-WordPressInstall {
    param([string]$phpExe, [string]$phpIni, [string]$wpcliPhar)
    Write-Step "Running WordPress core install via WP-CLI"

    $env:HOME = $Root   # WP-CLI needs HOME on Windows

    $prevEA = $ErrorActionPreference; $ErrorActionPreference = "Continue"

    $check = & $phpExe -c $phpIni $wpcliPhar --path="$WpDir" --allow-root core is-installed 2>&1
    if ($LASTEXITCODE -eq 0) {
        $ErrorActionPreference = $prevEA
        Write-OK "WordPress already installed - skipping"
        return
    }

    Write-Host "  Running wp core install..."
    & $phpExe -c $phpIni $wpcliPhar --path="$WpDir" --allow-root `
        core install `
        ("--url=" + $WP_SITE_URL) `
        ("--title=" + $WP_SITE_TITLE) `
        ("--admin_user=" + $WP_ADMIN) `
        ("--admin_password=" + $WP_ADMIN_PASS) `
        ("--admin_email=" + $WP_ADMIN_EMAIL) `
        --skip-email 2>&1

    $ErrorActionPreference = $prevEA

    if ($LASTEXITCODE -eq 0) {
        Write-OK "WordPress installation complete"
    } else {
        Write-WARN "WP-CLI install returned non-zero - may still work on first browser visit"
    }
}

# ---------------------------------------------------------------------------
# PHP router for built-in server (WordPress URL rewrite compat)
# ---------------------------------------------------------------------------

function Write-RouterPHP {
    Ensure-Dir $ScriptsDir
    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.Add("<?php")
    $lines.Add("/**")
    $lines.Add(" * PHP built-in server router for WordPress.")
    $lines.Add(" * Handles URL rewriting so pretty permalinks work without Apache.")
    $lines.Add(" */")
    $lines.Add('$request = $_SERVER[' + "'REQUEST_URI'];")
    $lines.Add('$path    = parse_url($request, PHP_URL_PATH);')
    $lines.Add('$file    = __DIR__ . "/../wordpress" . $path;')
    $lines.Add("")
    $lines.Add("// Serve real static files directly")
    $lines.Add('if ($path !== "/" && is_file($file)) {')
    $lines.Add("    return false;")
    $lines.Add("}")
    $lines.Add("")
    $lines.Add("// Directories with their own front controller (wp-admin/, etc.)")
    $lines.Add('if ($path !== "/" && is_dir($file)) {')
    $lines.Add('    $index = rtrim($file, "/\\") . DIRECTORY_SEPARATOR . "index.php";')
    $lines.Add("    if (is_file(\$index)) {")
    $lines.Add("        chdir(dirname(\$index));")
    $lines.Add("        require \$index;")
    $lines.Add("        return true;")
    $lines.Add("    }")
    $lines.Add("}")
    $lines.Add("")
    $lines.Add("// Route everything else through WordPress index")
    $lines.Add('$_SERVER["SCRIPT_FILENAME"] = __DIR__ . "/../wordpress/index.php";')
    $lines.Add('$_SERVER["SCRIPT_NAME"]     = "/index.php";')
    $lines.Add("require __DIR__ . '/../wordpress/index.php';")

    Write-UTF8 (Join-Path $ScriptsDir "router.php") ($lines -join [Environment]::NewLine)
    Write-OK "PHP router written: scripts\router.php"
}

# ---------------------------------------------------------------------------
# Start script
# ---------------------------------------------------------------------------

function Write-StartScript {
    param([string]$phpExe, [string]$phpIni, [string]$mysqldExe, [string]$myIni)

    Ensure-Dir $ScriptsDir

    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.Add("# WebMaker - Start local development environment")
    $lines.Add("# Usage:  .\scripts\start.ps1")
    $lines.Add("# Stop:   Ctrl+C (stops PHP; MariaDB keeps running)")
    $lines.Add("")
    $lines.Add('$Root      = Split-Path $PSScriptRoot -Parent')
    $lines.Add('$PhpExe    = "' + $phpExe.Replace('\', '\\') + '"')
    $lines.Add('$PhpIni    = "' + $phpIni.Replace('\', '\\') + '"')
    $lines.Add('$MysqldExe = "' + $mysqldExe.Replace('\', '\\') + '"')
    $lines.Add('$MyIni     = "' + $myIni.Replace('\', '\\') + '"')
    $lines.Add('$WpDir     = Join-Path $Root "wordpress"')
    $lines.Add('$Router    = Join-Path $Root "scripts\router.php"')
    $lines.Add('$DB_PORT   = ' + $DB_PORT)
    $lines.Add('$WP_PORT   = ' + $WP_PORT)
    $lines.Add("")
    $lines.Add("function Wait-TCP {")
    $lines.Add('    param([int]$Port, [int]$Sec = 20)')
    $lines.Add('    $end = (Get-Date).AddSeconds($Sec)')
    $lines.Add('    while ((Get-Date) -lt $end) {')
    $lines.Add("        try {")
    $lines.Add('            $c = New-Object System.Net.Sockets.TcpClient')
    $lines.Add('            $c.Connect("127.0.0.1", $Port)')
    $lines.Add('            $c.Close()')
    $lines.Add("            return `$true")
    $lines.Add("        } catch { Start-Sleep -Milliseconds 400 }")
    $lines.Add("    }")
    $lines.Add("    return `$false")
    $lines.Add("}")
    $lines.Add("")
    $lines.Add('Write-Host ""')
    $lines.Add('Write-Host "  WebMaker Dev Environment" -ForegroundColor Magenta')
    $lines.Add('Write-Host ""')
    $lines.Add("")
    $lines.Add("# Start MariaDB if not already running")
    $lines.Add('if (Wait-TCP $DB_PORT 2) {')
    $lines.Add('    Write-Host "  [OK] MariaDB already running on port $DB_PORT" -ForegroundColor Green')
    $lines.Add("} else {")
    $lines.Add('    Write-Host "  Starting MariaDB on port $DB_PORT..." -ForegroundColor Yellow')
    $lines.Add('    Start-Process -FilePath $MysqldExe -ArgumentList ("--defaults-file=`"" + $MyIni + "`"") -NoNewWindow')
    $lines.Add('    if (Wait-TCP $DB_PORT 30) {')
    $lines.Add('        Write-Host "  [OK] MariaDB started" -ForegroundColor Green')
    $lines.Add("    } else {")
    $lines.Add('        Write-Host "  [FAIL] MariaDB failed to start" -ForegroundColor Red; exit 1')
    $lines.Add("    }")
    $lines.Add("}")
    $lines.Add("")
    $lines.Add('Write-Host ""')
    $lines.Add('Write-Host "  WordPress : http://localhost:$WP_PORT" -ForegroundColor Cyan')
    $lines.Add('Write-Host "  Admin     : http://localhost:$WP_PORT/wp-admin" -ForegroundColor Cyan')
    $lines.Add('Write-Host "  Username  : ' + $WP_ADMIN + '" -ForegroundColor Cyan')
    $lines.Add('Write-Host "  Password  : ' + $WP_ADMIN_PASS + '" -ForegroundColor Cyan')
    $lines.Add('Write-Host ""')
    $lines.Add('Write-Host "  Press Ctrl+C to stop the web server." -ForegroundColor Gray')
    $lines.Add('Write-Host ""')
    $lines.Add("")
    $lines.Add("# Start PHP built-in server (use -c to bypass any system PHP registry)")
    $lines.Add('# Bind to 127.0.0.1 so IPv4 health checks (verify.py) work on Windows')
    $lines.Add('& $PhpExe -c $PhpIni -S ("127.0.0.1:" + $WP_PORT) -t $WpDir $Router')

    Write-UTF8 (Join-Path $ScriptsDir "start.ps1") ($lines -join [Environment]::NewLine)
    Write-OK "Start script written: scripts\start.ps1"
}

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "==========================================" -ForegroundColor Magenta
Write-Host "  WebMaker - Environment Setup"             -ForegroundColor Magenta
Write-Host "==========================================" -ForegroundColor Magenta

Ensure-Dir $LogDir
Ensure-Dir $BinDir
Ensure-Dir $ScriptsDir

# 1. PHP
if (-not $SkipPHP) {
    $phpExe = Find-PHP
    if ($phpExe -and -not $Force) {
        Write-OK "PHP found: $phpExe"
        # Re-apply ini config (handles case where PHP was installed in a previous run)
        Configure-PhpIni $PhpDir
    } else {
        $phpExe = Install-PHP   # Configure-PhpIni is called inside Install-PHP
    }
} else {
    $phpExe = Find-PHP
    if (-not $phpExe) { throw "PHP not found and -SkipPHP was set" }
    Configure-PhpIni $PhpDir
}

# Always reference the ini explicitly to bypass any system-wide PHP registry settings
$phpIni = Get-PhpIni
# Quick sanity check — use -c to force our ini
$phpVer = & $phpExe -c $phpIni -r "echo PHP_VERSION;" 2>&1
Write-OK "PHP version: $phpVer"

# 2. MariaDB
$mysqldExe = $null
$myIni     = $null

if (-not $SkipDB) {
    $mysqldExe = Find-MySQLd
    if ($mysqldExe -and -not $Force) {
        Write-OK "MySQL/MariaDB found: $mysqldExe"
    } else {
        $mysqldExe = Install-MariaDB
    }
    # Always run Initialize-MariaDB - it skips if mysql/ already exists
    $myIni = Initialize-MariaDB $mysqldExe
    Start-MariaDB  $mysqldExe $myIni
    Setup-Database $mysqldExe
} else {
    $mysqldExe = Find-MySQLd
    $myIni     = Join-Path $DbDir "my.ini"
}

# 3. WordPress
if (-not $SkipWordPress) {
    Install-WordPress
    Write-WpConfig
}

# 4. WP-CLI
$wpcliPhar = Install-WpCLI

# 5. Complete WordPress install
if (-not $SkipWordPress -and $mysqldExe) {
    Complete-WordPressInstall $phpExe $phpIni $wpcliPhar
}

# 6. Scripts
Write-RouterPHP
if ($mysqldExe -and $myIni) {
    Write-StartScript $phpExe $phpIni $mysqldExe $myIni
}

# Summary
Write-Host ""
Write-Host "==========================================" -ForegroundColor Green
Write-Host "  Setup Complete!"                          -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
Write-Host "  WordPress URL  : http://localhost:$WP_PORT" -ForegroundColor Cyan
Write-Host "  Admin URL      : http://localhost:$WP_PORT/wp-admin" -ForegroundColor Cyan
Write-Host "  Username       : $WP_ADMIN"                  -ForegroundColor Cyan
Write-Host "  Password       : $WP_ADMIN_PASS"             -ForegroundColor Cyan
Write-Host "  Database       : $DB_NAME  (port $DB_PORT)"  -ForegroundColor Cyan
Write-Host ""
Write-Host "  Start server:  .\scripts\start.ps1"       -ForegroundColor Yellow
Write-Host "  Verify setup:  .\.venv\Scripts\python.exe setup\verify.py"  -ForegroundColor Yellow
Write-Host ""
