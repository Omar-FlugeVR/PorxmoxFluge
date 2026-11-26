<?php
declare(strict_types=1);

use Homelab\Auth;
use Homelab\ConfigRepository;
use Homelab\CaddyRenderer;
use Homelab\HttpError;
use Homelab\InfrastructureSynchronizer;
use Homelab\InventoryRepository;
use Homelab\JsonStore;
use Homelab\NetworkScanner;
use Homelab\NdiMonitor;
use Homelab\NdiRuntime;
use Homelab\UsersRepository;
use Homelab\UpdateJobs;
use Homelab\Validator;

require dirname(__DIR__) . '/src/autoload.php';

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');
ini_set('display_errors', '0');
ini_set('session.use_strict_mode', '1');
ini_set('session.use_only_cookies', '1');
session_name('FLUGE_CONTROL_SESSION');
session_set_cookie_params(['httponly' => true, 'secure' => isset($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off', 'samesite' => 'Strict', 'path' => '/']);

try {
    if (!session_start()) {
        throw new HttpError(503, 'No se puede iniciar la sesión.');
    }
    $store = new JsonStore(getenv('FLUGE_STATE_DIR') ?: '/var/lib/fluge-control');
    $config = new ConfigRepository($store);
    $inventory = new InventoryRepository($store);
    $auth = new Auth($store, $config);
    $users = new UsersRepository($store, $config);
    $method = $_SERVER['REQUEST_METHOD'] ?? 'GET';
    $input = [];
    if ($method === 'POST') {
        $origin = $_SERVER['HTTP_ORIGIN'] ?? null;
        if ($origin !== null) {
            $expected = ((isset($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off') ? 'https://' : 'http://') . ($_SERVER['HTTP_HOST'] ?? '');
            if (!hash_equals(strtolower($expected), strtolower($origin))) {
                throw new HttpError(403, 'Origen de la petición no permitido.');
            }
        }
        $contentType = strtolower(trim(explode(';', $_SERVER['CONTENT_TYPE'] ?? '')[0]));
        if ($contentType !== 'application/json') {
            throw new HttpError(415, 'Las peticiones deben utilizar application/json.');
        }
        if ((int) ($_SERVER['CONTENT_LENGTH'] ?? 0) > 4194304) {
            throw new HttpError(413, 'La petición supera el máximo de 4 MiB.');
        }
        $raw = file_get_contents('php://input', false, null, 0, 4194305);
        if ($raw === false || strlen($raw) > 4194304) {
            throw new HttpError(413, 'La petición supera el máximo de 4 MiB.');
        }
        try {
            $body = json_decode($raw, false, 64, JSON_THROW_ON_ERROR);
            if (!$body instanceof stdClass) {
                throw new HttpError(400, 'Se esperaba un objeto JSON.');
            }
            $input = json_decode($raw, true, 64, JSON_THROW_ON_ERROR);
        } catch (JsonException $error) {
            throw new HttpError(400, 'JSON no válido.');
        }
    } elseif ($method !== 'GET') {
        header('Allow: GET, POST');
        throw new HttpError(405, 'Método no permitido.');
    }
    $action = $_GET['ndi_action'] ?? $input['action'] ?? $_GET['action'] ?? ($method === 'GET' ? 'data' : '');
    if (!is_string($action)) {
        throw new HttpError(400, 'Acción no válida.');
    }
    $remoteAddress = $_SERVER['REMOTE_ADDR'] ?? 'unknown';
    if ($method === 'GET' && $action === 'session') {
        $response = $auth->session() + ['demo' => getenv('FLUGE_DEMO') === '1'];
        if (!$response['authenticated']) {
            $response['login_retry_after'] = $auth->loginRetryAfter($remoteAddress);
        }
    } elseif ($method === 'POST' && $action === 'login') {
        $response = $auth->login($input, $remoteAddress);
    } else {
        $auth->requireAuthenticated();
        if ($method === 'POST') {
            $auth->csrf($_SERVER['HTTP_X_CSRF_TOKEN'] ?? null);
        }
        if (!in_array($method . ':' . $action, ['GET:data', 'GET:runtime', 'GET:ndi_status', 'POST:logout', 'POST:change_password'], true)) {
            $auth->requireAdmin();
        }
        switch ($method . ':' . $action) {
            case 'GET:data':
                $response = $inventory->get();
                break;
            case 'GET:runtime':
                $current = $config->get();
                $response = ['demo' => getenv('FLUGE_DEMO') === '1', 'installationName' => $current['installationName'], 'baseDomain' => $current['baseDomain'], 'dashboardDomain' => $current['dashboardDomain'], 'lastSync' => $store->read('last-sync.json') ?: null, 'ndi' => $current['ndi'], 'ndi_mock' => NdiMonitor::mock()];
                break;
            case 'GET:updates':
                $response = (new UpdateJobs($store))->report();
                break;
            case 'POST:update_service':
                $response = (new UpdateJobs($store))->enqueue($input);
                break;
            case 'GET:ndi_status':
            case 'POST:ndi_test':
                session_write_close();
                $monitor = new NdiMonitor($store, $config);
                $response = $method === 'GET' ? $monitor->status() : $monitor->test($input);
                break;
            case 'POST:ndi_clear_log':
                session_write_close();
                $response = (new NdiMonitor($store, $config))->clearLog();
                break;
            case 'POST:ndi_runtime_prepare':
            case 'POST:ndi_runtime_install':
                session_write_close();
                $runtime = new NdiRuntime($store);
                $response = $action === 'ndi_runtime_prepare' ? $runtime->prepare() : $runtime->install($input);
                break;
            case 'GET:applied':
                if (getenv('FLUGE_DEMO') !== '1') throw new HttpError(404, 'Vista disponible en la muestra.');
                $response = ['dns' => $store->read('demo-dns.json'), 'caddy' => $store->read('demo-routes.json')['content'] ?? ''];
                break;
            case 'GET:get_config':
                $response = $config->publicConfig();
                break;
            case 'GET:get_scanner_config':
                $response = $config->scannerConfig();
                break;
            case 'GET:users':
                $response = $users->publicList();
                break;
            case 'GET:sync_plan':
                $response = (new InfrastructureSynchronizer($store, $inventory, $config))->plan();
                break;
            case 'POST:save_data':
                CaddyRenderer::plan(Validator::inventory($input)['services'], $config->get());
                $saved = $inventory->save($input);
                session_write_close();
                $sync = (new InfrastructureSynchronizer($store, $inventory, $config))->afterSave($saved['revision']);
                $response = ['status' => $sync['ok'] ? 'ok' : 'partial', 'revision' => $saved['revision'], 'updatedAt' => $saved['updatedAt'], 'dns_sync' => $sync];
                break;
            case 'POST:save_config':
                $saved = $config->save($input);
                $reauthenticationRequired = !$auth->authenticated();
                session_write_close();
                $sync = (new InfrastructureSynchronizer($store, $inventory, $config))->afterSave($inventory->get()['revision']);
                $response = ['status' => $sync['ok'] ? 'ok' : 'partial', 'config' => $saved, 'configRevision' => $saved['configRevision'], 'reauthenticationRequired' => $reauthenticationRequired, 'dns_sync' => $sync];
                break;
            case 'POST:save_category':
            case 'POST:delete_category':
                $categories = new Homelab\CategoryManager($inventory);
                $saved = $action === 'save_category' ? $categories->save($input) : $categories->delete($input);
                session_write_close();
                $sync = (new InfrastructureSynchronizer($store, $inventory, $config))->afterSave($saved['revision']);
                $response = ['status' => $sync['ok'] ? 'ok' : 'partial', 'inventory' => $saved, 'dns_sync' => $sync];
                break;
            case 'POST:logout':
                $response = $auth->logout();
                break;
            case 'POST:change_password':
                $response = $auth->changePassword($input);
                break;
            case 'POST:create_user':
                $response = $users->create($input, $auth->userId());
                break;
            case 'POST:update_user':
                $response = $users->update($input, $auth->userId());
                $response['reauthenticationRequired'] = !$auth->authenticated();
                break;
            case 'POST:delete_user':
                $response = $users->delete($input, $auth->userId());
                break;
            case 'POST:scan_chunk':
                $allowed = $config->get()['scanAllowedCidrs'];
                $targets = NetworkScanner::validateTargets($input['ips'] ?? null, $allowed);
                session_write_close();
                if (getenv('FLUGE_DEMO') === '1') {
                    // No sockets, ARP or DNS queries are executed in the local sample.
                    $peers = [10 => ['Proxmox VE', [8006,22]], 11 => ['AdGuard Home', [80,53]], 12 => ['Control Fluge', [80]], 15 => ['Companion', [8000,22]], 16 => ['NDI Discovery', [5959]], 30 => ['Mezclador de vídeo', [80,8080]], 31 => ['Procesador de audio', [80,22]], 42 => ['Switch de control', [80,443,22]], 105 => ['Portátil producción', [445]]];
                    $results = [];
                    foreach ($targets as $ip) {
                        $last = (int) substr($ip, strrpos($ip, '.') + 1);
                        $extraPeers = ['192.168.20.70' => ['Codificador de vídeo', [80,8080]], '192.168.20.110' => ['Procesador de vídeo', [80]], '192.168.30.50' => ['Consola de iluminación', [80,22]]];
                        $peer = $extraPeers[$ip] ?? (str_starts_with($ip, '192.168.1.') ? ($peers[$last] ?? null) : null);
                        $results[] = ['ip' => $ip, 'isAlive' => $peer !== null, 'hostname' => $peer[0] ?? '', 'mac' => $peer ? sprintf('02:46:4C:55:47:%02X', $last) : '', 'ports' => $peer[1] ?? [], 'latency' => $peer ? 1.2 : null];
                    }
                    usleep(100000);
                    $response = ['results' => $results, 'count' => count($results), 'elapsed' => 0.1, 'demo' => true];
                } else $response = (new NetworkScanner())->scan($targets, $allowed);
                break;
            case 'POST:sync_infrastructure':
                $revision = Validator::revision($input['revision'] ?? null);
                session_write_close();
                $sync = (new InfrastructureSynchronizer($store, $inventory, $config))->sync($revision);
                if (!$sync['ok']) {
                    http_response_code(502);
                }
                $response = ['status' => $sync['ok'] ? 'ok' : 'error', 'dns_sync' => $sync];
                break;
            default:
                throw new HttpError(404, 'Acción no encontrada.');
        }
    }
    echo json_encode($response, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
} catch (HttpError $error) {
    http_response_code($error->status);
    if ($error->status === 429 && isset($error->details['retry_after'])) {
        header('Retry-After: ' . $error->details['retry_after']);
    }
    echo json_encode(['status' => 'error', 'message' => $error->getMessage()] + $error->details, JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE);
} catch (Throwable $error) {
    error_log('Homelab API error: ' . get_class($error) . ': ' . $error->getMessage());
    http_response_code(500);
    echo json_encode(['status' => 'error', 'message' => 'Error interno del servidor. Consulta el registro.']);
} finally {
    if (session_status() === PHP_SESSION_ACTIVE) {
        session_write_close();
    }
}
