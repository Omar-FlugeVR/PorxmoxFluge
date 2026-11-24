<?php
declare(strict_types=1);
namespace Homelab;

/** Category changes and every affected reference are saved in one inventory revision. */
final class CategoryManager
{
    public function __construct(private InventoryRepository $inventory) {}

    private function snapshot(array $input): array
    {
        $revision = Validator::revision($input['revision'] ?? null);
        $current = $this->inventory->get();
        if ($current['revision'] !== $revision) throw new HttpError(409, 'El inventario ha cambiado. Recarga antes de modificar las categorías.');
        return $current;
    }

    public function save(array $input): array
    {
        $data = $this->snapshot($input);
        $id = Validator::text($input['id'] ?? '', 'id', 100);
        $name = Validator::text($input['name'] ?? null, 'name', 120, true);
        $index = null;
        foreach ($data['categories'] as $number => $category) {
            if ($category['id'] === $id) $index = $number;
            elseif (preg_match('/^' . preg_quote($name, '/') . '$/iuD', $category['name'])) throw new HttpError(422, 'Ya existe una categoría con ese nombre.');
        }
        if ($id !== '' && $index === null) throw new HttpError(404, 'La categoría ya no existe.');
        if ($index === null) {
            $data['categories'][] = ['id' => 'cat-' . bin2hex(random_bytes(8)), 'name' => $name, 'color' => '#6db9b2'];
        } else {
            $oldName = $data['categories'][$index]['name'];
            $data['categories'][$index]['name'] = $name;
            foreach ($data['services'] as &$service) if ($service['category'] === $oldName) $service['category'] = $name;
            unset($service);
            foreach ($data['ips'] as &$host) if ($host['tag'] === $oldName) $host['tag'] = $name;
            unset($host);
        }
        return $this->inventory->save($data);
    }

    public function delete(array $input): array
    {
        $data = $this->snapshot($input);
        $id = Validator::text($input['id'] ?? null, 'id', 100, true);
        $replacementId = Validator::text($input['replacementId'] ?? '', 'replacementId', 100);
        $category = null;
        $replacement = null;
        foreach ($data['categories'] as $item) {
            if ($item['id'] === $id) $category = $item;
            elseif ($item['id'] === $replacementId) $replacement = $item;
        }
        if ($category === null) throw new HttpError(404, 'La categoría ya no existe.');
        if (count($data['categories']) < 2) throw new HttpError(422, 'Debes conservar al menos una categoría.');
        $inUse = array_filter($data['services'], fn($service) => $service['category'] === $category['name'])
            || array_filter($data['ips'], fn($host) => $host['tag'] === $category['name']);
        if (($inUse || $replacementId !== '') && $replacement === null) throw new HttpError(422, 'Selecciona otra categoría para los servicios y equipos asociados.');
        foreach ($data['services'] as &$service) if ($service['category'] === $category['name']) $service['category'] = $replacement['name'];
        unset($service);
        foreach ($data['ips'] as &$host) if ($host['tag'] === $category['name']) $host['tag'] = $replacement['name'];
        unset($host);
        $data['categories'] = array_values(array_filter($data['categories'], fn($item) => $item['id'] !== $id));
        return $this->inventory->save($data);
    }
}
