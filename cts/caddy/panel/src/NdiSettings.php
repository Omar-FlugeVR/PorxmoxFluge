<?php
declare(strict_types=1);
namespace Homelab;

final class NdiSettings
{
    public static function defaults(): array
    {
        return ['enabled' => false, 'ip' => '', 'port' => 5959, 'polling_rate' => 5, 'timeout' => 3, 'groups' => '', 'mock_scenario' => 'normal'];
    }

    public static function validate(mixed $input): array
    {
        if (!is_array($input) || array_is_list($input)) throw new HttpError(422, 'Los ajustes NDI deben ser un objeto.');
        $settings = $input + self::defaults();
        if (!is_bool($settings['enabled'])) throw new HttpError(422, 'NDI enabled debe ser booleano.');
        $host = Validator::text($settings['ip'], 'IP / Host NDI', 253, $settings['enabled']);
        if ($host !== '' && !filter_var($host, FILTER_VALIDATE_IP)) {
            if (preg_match('/^[0-9.]+$/D', $host) || !preg_match('/^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$/D', $host)) throw new HttpError(422, 'Introduce una IP o un hostname válido, sin protocolo ni puerto.');
            foreach (explode('.', $host) as $label) if (!preg_match('/^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/D', $label)) throw new HttpError(422, 'El hostname NDI no es válido.');
        }
        $settings['ip'] = strtolower($host);
        foreach (['port' => [1,65535], 'polling_rate' => [2,300], 'timeout' => [1,15]] as $name => [$min,$max]) {
            if (!is_int($settings[$name]) || $settings[$name] < $min || $settings[$name] > $max) throw new HttpError(422, "NDI {$name} debe ser un entero entre {$min} y {$max}.");
        }
        $groups = Validator::text($settings['groups'], 'Grupos NDI', 2048);
        if (preg_match('/[\x00-\x1f\x7f]/', $settings['groups'])) throw new HttpError(422, 'Separa los grupos NDI con comas, en una sola línea.');
        $groups = array_values(array_unique(array_filter(array_map('trim', explode(',', $groups)), fn($group) => $group !== '')));
        if (count($groups) > 32) throw new HttpError(422, 'Configura como máximo 32 grupos NDI.');
        foreach ($groups as $group) if (preg_match('/[\x00-\x1f\x7f]/', $group) || preg_match_all('/./us', $group) > 64) throw new HttpError(422, 'Cada grupo NDI admite hasta 64 caracteres en una línea.');
        $settings['groups'] = implode(',', $groups);
        if (!in_array($settings['mock_scenario'], ['normal','offline','empty','missing_lib'], true)) throw new HttpError(422, 'El escenario NDI no es válido.');
        return array_intersect_key($settings, self::defaults());
    }

    public static function server(array $settings): string
    {
        return (str_contains($settings['ip'], ':') ? '[' . $settings['ip'] . ']' : $settings['ip']) . ':' . $settings['port'];
    }

    public static function sdkConfiguration(array $settings): array
    {
        $ndi = ['networks' => ['discovery' => self::server($settings), 'ips' => '']];
        if ($settings['groups'] !== '') $ndi['groups'] = ['recv' => $settings['groups']];
        return ['ndi' => $ndi];
    }
}
