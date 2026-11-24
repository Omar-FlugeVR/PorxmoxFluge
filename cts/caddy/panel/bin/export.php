<?php
declare(strict_types=1);
use Homelab\{JsonStore, InventoryRepository, ConfigRepository, CaddyRenderer};
require dirname(__DIR__) . '/src/autoload.php';
if (PHP_SAPI !== 'cli') exit(1);
$store = new JsonStore(getenv('FLUGE_STATE_DIR') ?: '/var/lib/fluge-control');
$inventory = (new InventoryRepository($store))->get();
$config = (new ConfigRepository($store))->get();
if (($argv[1] ?? '') === 'routes') {
    echo CaddyRenderer::renderCandidate(CaddyRenderer::plan($inventory['services'], $config)['candidate'], [$config['dashboardDomain']]);
    exit;
}
$services = [];
foreach ($inventory['services'] as $service) {
    if ($service['domain'] === '') continue;
    if (!str_ends_with($service['domain'], '.' . $config['baseDomain'])) throw new RuntimeException('Dominio fuera del despliegue.');
    $subdomain = substr($service['domain'], 0, -strlen('.' . $config['baseDomain']));
    $services[] = ['name' => preg_replace('/\s+/', ' ', $service['name']), 'subdomain' => $subdomain, 'target' => $service['url'], 'category' => preg_replace('/\s+/', ' ', $service['category']) ?: 'Equipos', 'description' => preg_replace('/\s+/', ' ', $service['description']) ?: 'Sin descripción', 'mode' => $service['proxyMode'], 'ip' => $service['ip'] ?? ''];
}
echo json_encode($services, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
