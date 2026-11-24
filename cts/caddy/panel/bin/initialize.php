<?php
declare(strict_types=1);
use Homelab\{JsonStore, Validator, PasswordPolicy, CaddyRenderer};
require dirname(__DIR__) . '/src/autoload.php';
if (PHP_SAPI !== 'cli' || !($argc === 5 || ($argc === 3 && $argv[2] === '--stdin'))) exit("Uso: php initialize.php STATE SEED CONFIG AUTH | STATE --stdin\n");
$directory = $argv[1];
$store = new JsonStore($directory);
if ($argc === 3) {
    [$seed, $config, $credentials] = json_decode(stream_get_contents(STDIN), true, 64, JSON_THROW_ON_ERROR);
} else {
    $seed = json_decode(file_get_contents($argv[2]), true, 64, JSON_THROW_ON_ERROR);
    $config = json_decode(file_get_contents($argv[3]), true, 64, JSON_THROW_ON_ERROR);
    $credentials = json_decode(file_get_contents($argv[4]), true, 64, JSON_THROW_ON_ERROR);
}
if (is_file($store->path('config.json'))) {
    $current = $store->read('config.json');
    foreach (['baseDomain', 'dashboardDomain', 'serverProxyIp'] as $key) {
        if (($current[$key] ?? null) !== $config[$key]) throw new RuntimeException('La red o el dominio del panel han cambiado. Migra su configuración privada antes de sincronizar.');
    }
}
$store->locked('inventory', function () use ($store, $seed): void {
    if (!is_file($store->path('inventory.json'))) $store->write('inventory.json', Validator::inventory($seed) + ['version' => '2.0', 'revision' => 1, 'updatedAt' => gmdate('c')]);
});
if (!is_file($store->path('config.json'))) $store->write('config.json', $config + ['configRevision' => 1]);
if (!is_file($store->path('auth.json'))) $store->write('auth.json', ['mode' => 'independent', 'username' => $credentials['username'], 'passwordHash' => PasswordPolicy::hash($credentials['password']), 'authRevision' => 1]);
// Installer-created DNS entries belong to this deployment. Later external entries remain untouched.
if (!is_file($store->path('managed-dns.json'))) {
    $owned = [];
    foreach (CaddyRenderer::plan($seed['services'], $config)['desiredDns'] as $domain => $answer) $owned[] = compact('domain', 'answer');
    $store->write('managed-dns.json', $owned);
}
echo "Inventario inicializado; datos existentes conservados.\n";
