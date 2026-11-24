<?php
declare(strict_types=1);
namespace Homelab;

final class InventoryRepository
{
    public function __construct(private JsonStore $store) {}

    public function get(): array
    {
        $data = $this->store->read('inventory.json', ['services' => [], 'ips' => [], 'categories' => [], 'version' => '2.0', 'revision' => 0, 'updatedAt' => null]);
        if (!isset($data['revision']) || !is_int($data['revision'])) {
            throw new HttpError(503, 'El inventario necesita una migración de versión.');
        }
        return $data;
    }

    public function save(array $input): array
    {
        $revision = Validator::revision($input['revision'] ?? null);
        $data = Validator::inventory($input);
        return $this->store->locked('inventory', function () use ($revision, $data): array {
            $current = $this->get();
            if ($revision !== $current['revision']) {
                throw new HttpError(409, 'El inventario ha cambiado en otra sesión. Recarga antes de guardar.', ['revision' => $current['revision']]);
            }
            $next = $data + ['version' => '2.0', 'revision' => $revision + 1, 'updatedAt' => gmdate('c')];
            $this->store->write('inventory.json', $next);
            return $next;
        });
    }
}
