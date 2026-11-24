<?php
declare(strict_types=1);
// Router for the loopback-only PHP development server; never installed as a service.
if (getenv('FLUGE_DEMO') !== '1') { http_response_code(503); exit('Demo desactivada.'); }
$path = rawurldecode(parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH));
$public = realpath(dirname(__DIR__) . '/public');
$ndiRoutes = ['/api/ndi/status' => 'ndi_status', '/api/ndi/test' => 'ndi_test', '/api/ndi/log/clear' => 'ndi_clear_log'];
if (isset($ndiRoutes[$path])) { $_GET['ndi_action'] = $ndiRoutes[$path]; $path = '/api.php'; }
$file = realpath($public . ($path === '/' ? '/index.html' : $path));
if (!$file || !str_starts_with($file, $public . DIRECTORY_SEPARATOR) || !is_file($file) || (str_ends_with($file, '.php') && $path !== '/api.php')) { http_response_code(404); exit; }
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');
header('Referrer-Policy: same-origin');
header('X-Frame-Options: DENY');
if ($path === '/api.php') require $file;
else {
    $type = ['html' => 'text/html; charset=utf-8', 'js' => 'text/javascript; charset=utf-8', 'css' => 'text/css; charset=utf-8', 'svg' => 'image/svg+xml'][pathinfo($file, PATHINFO_EXTENSION)] ?? 'application/octet-stream';
    header('Content-Type: ' . $type); readfile($file);
}
