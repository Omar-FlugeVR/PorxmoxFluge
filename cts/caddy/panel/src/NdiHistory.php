<?php
declare(strict_types=1);
namespace Homelab;

/** Observations, not a claim about devices while discovery is unavailable or paused. */
final class NdiHistory
{
    public function __construct(private JsonStore $store) {}

    private static function scopeId(array $settings, bool $mock): string
    {
        return hash('sha256', json_encode([NdiSettings::server($settings), $settings['groups'], $mock], JSON_THROW_ON_ERROR));
    }

    /** Caller holds ndi.lock; only clear events for the selected server/groups. */
    public function clearLog(array $settings, bool $mock): void
    {
        $all = $this->store->read('ndi-history.json', ['scopes' => []]);
        $scopeId = self::scopeId($settings, $mock);
        if (isset($all['scopes'][$scopeId])) {
            $all['scopes'][$scopeId]['events'] = [];
            $this->store->write('ndi-history.json', $all);
        }
    }

    /** Called only for fresh probes, while NdiMonitor holds ndi.lock. */
    public function record(array $settings, array $status): array
    {
        $all = $this->store->read('ndi-history.json', ['scopes' => []]);
        $scopeId = self::scopeId($settings, $status['mock']);
        $scope = $all['scopes'][$scopeId] ?? ['entities' => [], 'events' => [], 'server_online' => null,
            'server_last_seen' => null, 'warning' => null, 'receivers_warning' => null];
        $now = $status['last_updated'];
        $event = function (string $type, string $message, ?array $entity = null, string $level = 'info') use (&$scope, $now): void {
            $scope['events'][] = ['id' => bin2hex(random_bytes(8)), 'at' => $now, 'type' => $type, 'message' => $message,
                'host' => $entity['host'] ?? null, 'direction' => $entity['direction'] ?? null, 'level' => $level];
        };
        if ($scope['server_online'] !== $status['online']) {
            $event($status['online'] ? 'server_online' : 'server_offline', $status['online'] ? 'Discovery Server disponible.' : 'Discovery Server sin respuesta; el estado de los equipos queda sin comprobar.', null, $status['online'] ? 'info' : 'warning');
        }
        $scope['server_online'] = $status['online'];
        if ($status['online']) $scope['server_last_seen'] = $now;
        foreach (['warning','receivers_warning'] as $field) {
            $value = $status[$field] ?? null;
            if ($value !== $scope[$field]) {
                if ($value) $event($field, $value, null, 'warning');
                elseif ($scope[$field]) $event('query_recovered', $field === 'warning' ? 'La consulta de fuentes vuelve a estar disponible.' : 'La consulta de receptores vuelve a estar disponible.');
                $scope[$field] = $value;
            }
        }
        foreach (['sender' => 'sources', 'receiver' => 'receivers'] as $direction => $collection) {
            $observed = $status['online'] && ($status[$collection === 'sources' ? 'sources_observed' : 'receivers_observed'] ?? false);
            $seen = [];
            if ($observed) foreach ($status[$collection] as $item) {
                $identity = $direction === 'receiver' && !empty($item['id']) ? $item['id'] : $item['full_name'];
                $id = hash('sha256', $direction . "\0" . $identity);
                $seen[$id] = true;
                $old = $scope['entities'][$id] ?? null;
                $entity = ['id' => $id, 'direction' => $direction, 'host' => $item['host'], 'stream' => $item['stream'],
                    'full_name' => $item['full_name'], 'url' => $item['url'], 'available' => true,
                    'first_seen' => $old['first_seen'] ?? $now, 'last_seen' => $now, 'last_checked' => $now,
                    'last_changed' => $old && $old['available'] === true ? $old['last_changed'] : $now];
                $role = $direction === 'sender' ? 'Emisor' : 'Receptor';
                if (!$old) $event('entity_found', "$role detectado: {$item['full_name']}.", $entity);
                elseif ($old['available'] !== true) $event('entity_returned', "$role visible de nuevo: {$item['full_name']}.", $entity);
                if ($old && $old['url'] !== $item['url']) $event('address_changed', "Nueva dirección de {$item['full_name']}: {$item['url']}.", $entity);
                $scope['entities'][$id] = $entity;
            }
            foreach ($scope['entities'] as $id => &$entity) {
                if ($entity['direction'] !== $direction || isset($seen[$id])) continue;
                if (!$observed) { $entity['available'] = null; continue; }
                if ($entity['available'] !== false) {
                    $entity['available'] = false; $entity['last_changed'] = $now;
                    $event('entity_missing', 'Deja de estar visible: ' . $entity['full_name'] . '.', $entity, 'warning');
                }
                $entity['last_checked'] = $now;
            }
            unset($entity);
        }
        uasort($scope['entities'], static function (array $a, array $b): int {
            $priority = fn($item) => $item['available'] === true ? 0 : ($item['available'] === false ? 1 : 2);
            return ($priority($a) <=> $priority($b)) ?: strcmp($b['last_seen'], $a['last_seen']) ?: strcmp($a['full_name'], $b['full_name']);
        });
        $scope['entities'] = array_slice($scope['entities'], 0, 1000, true);
        $scope['events'] = array_slice($scope['events'], -500);
        $scope['updated_at'] = $now;
        // Keep separate histories across server/group changes and demo/real modes.
        unset($all['scopes'][$scopeId]); $all['scopes'][$scopeId] = $scope;
        while (count($all['scopes']) > 4) array_shift($all['scopes']);
        $this->store->write('ndi-history.json', $all);
        $entities = array_values($scope['entities']);
        $roles = [];
        foreach ($entities as $entity) if ($entity['available'] === true) $roles[strtolower($entity['host'])][$entity['direction']] = true;
        foreach ($entities as &$entity) $entity['device_role'] = count($roles[strtolower($entity['host'])] ?? []) > 1 ? 'both' : $entity['direction'];
        unset($entity);
        return ['entities' => $entities, 'events' => array_reverse($scope['events']), 'server_last_seen' => $scope['server_last_seen'], 'history_limit' => 500];
    }
}
