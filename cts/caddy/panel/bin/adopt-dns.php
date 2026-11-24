<?php
declare(strict_types=1);
// Trusted host synchronization hands over exactly the DNS rules it published.
use Homelab\{JsonStore, ConfigRepository, InventoryRepository, CaddyRenderer};
require dirname(__DIR__) . '/src/autoload.php';
if (PHP_SAPI !== 'cli') exit(1);
$store = new JsonStore(getenv('FLUGE_STATE_DIR') ?: '/var/lib/fluge-control');
$plan = json_decode(stream_get_contents(STDIN), true, 64, JSON_THROW_ON_ERROR);
$store->locked('infrastructure', fn() => $store->locked('config', fn() => $store->locked('inventory', function () use ($store, $plan): void {
    $config = (new ConfigRepository($store))->get();
    $inventory = (new InventoryRepository($store))->get();
    $desired = CaddyRenderer::plan($inventory['services'], $config)['desiredDns'];
    $owned = [];
    foreach ($store->read('managed-dns.json') as $entry) $owned[$entry['domain'] . "\0" . $entry['answer']] = $entry;
    foreach ($plan['rewrites'] as $entry) {
        if (($desired[$entry['domain']] ?? null) === $entry['answer']) {
            $owned[$entry['domain'] . "\0" . $entry['answer']] = ['domain' => $entry['domain'], 'answer' => $entry['answer']];
        }
    }
    $store->write('managed-dns.json', array_values($owned));
})));
