<?php
/**
 * PHP built-in server router for WordPress.
 * Handles URL rewriting so pretty permalinks work without Apache.
 */
$request = $_SERVER['REQUEST_URI'];
$path    = parse_url($request, PHP_URL_PATH);
$file    = __DIR__ . '/../wordpress' . $path;

// Serve real static files directly
if ($path !== '/' && is_file($file)) {
    return false;
}

// Directories with their own front controller (wp-admin/, etc.)
if ($path !== '/' && is_dir($file)) {
    $index = rtrim($file, '/\\') . DIRECTORY_SEPARATOR . 'index.php';
    if (is_file($index)) {
        chdir(dirname($index));
        require $index;
        return true;
    }
}

// Route everything else through WordPress index
$_SERVER['SCRIPT_FILENAME'] = __DIR__ . '/../wordpress/index.php';
$_SERVER['SCRIPT_NAME']     = '/index.php';
require __DIR__ . '/../wordpress/index.php';
