<?php
declare(strict_types=1);
namespace Homelab;

final class Validator
{
    public static function text(mixed $value, string $field, int $limit = 512, bool $required = false): string
    {
        if (!is_string($value) || strlen($value) > $limit || preg_match('/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/', $value)) {
            throw new HttpError(422, "Campo no válido: {$field}.");
        }
        $value = trim($value);
        if ($required && $value === '') {
            throw new HttpError(422, "Falta el campo {$field}.");
        }
        return $value;
    }

    public static function revision(mixed $value, string $field = 'revision'): int
    {
        if (!is_int($value) || $value < 0) {
            throw new HttpError(422, "{$field} debe ser un entero no negativo.");
        }
        return $value;
    }

    public static function domain(mixed $value, string $field = 'domain', bool $optional = false): string
    {
        $value = strtolower(self::text($value, $field, 253, !$optional));
        if ($optional && $value === '') {
            return '';
        }
        foreach (explode('.', $value) as $label) {
            if (!preg_match('/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/D', $label)) {
                throw new HttpError(422, "Dominio no válido: {$field}.");
            }
        }
        return $value;
    }

    public static function ip(mixed $value, string $field = 'ip'): string
    {
        $value = self::text($value, $field, 15, true);
        if (!filter_var($value, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) {
            throw new HttpError(422, "Dirección IPv4 no válida: {$field}.");
        }
        return $value;
    }

    /** Address-book entries may describe a subnet/range; probe targets may not. */
    public static function directoryAddress(mixed $value): string
    {
        $value = self::text($value, 'ip', 32, true);
        if (!preg_match('~^((?:[0-9]{1,3}\.){3}[0-9]{1,3})(?:-([0-9]{1,3}))?(?:/([0-9]{1,2}))?$~D', $value, $parts)) {
            throw new HttpError(422, 'Dirección o rango IPv4 no válido.');
        }
        self::ip($parts[1]);
        if (isset($parts[2]) && $parts[2] !== '') {
            $first = (int) substr($parts[1], strrpos($parts[1], '.') + 1);
            $last = (int) $parts[2];
            if ($last < $first || $last > 255) {
                throw new HttpError(422, 'El extremo del rango debe estar entre el primer octeto y 255.');
            }
        }
        if (isset($parts[3]) && (int) $parts[3] > 32) {
            throw new HttpError(422, 'El prefijo IPv4 debe estar entre 0 y 32.');
        }
        return $value;
    }

    /** Canonical URLs contain no Caddyfile interpolation or configuration tokens. */
    public static function url(mixed $value, string $field = 'url'): string
    {
        $value = self::text($value, $field, 2048, true);
        if (preg_match('/[\x00-\x20\x7f;{}#<>\\\\\x22\x27\x24\x60]/', $value)) {
            throw new HttpError(422, "URL no válida: {$field}. No se admiten espacios, fragmentos ni caracteres de configuración.");
        }
        if (!preg_match('~^[a-z][a-z0-9+.-]*://~i', $value)) {
            $value = (preg_match('/:(8006|9443|443)(?:\/|\?|$)/', $value) ? 'https://' : 'http://') . $value;
        }
        try {
            $parsed = parse_url($value);
        } catch (\ValueError $error) {
            throw new HttpError(422, "URL no válida: {$field}.");
        }
        if (!$parsed || !isset($parsed['host']) || isset($parsed['user']) || isset($parsed['pass']) || isset($parsed['fragment'])) {
            throw new HttpError(422, "URL no válida: {$field}.");
        }
        $scheme = strtolower($parsed['scheme'] ?? '');
        if (!in_array($scheme, ['http', 'https'], true)) {
            throw new HttpError(422, 'Solo se permiten direcciones HTTP o HTTPS.');
        }
        $host = strtolower($parsed['host']);
        if (str_starts_with($host, '[') && str_ends_with($host, ']')) {
            if (!filter_var(substr($host, 1, -1), FILTER_VALIDATE_IP, FILTER_FLAG_IPV6)) {
                throw new HttpError(422, "Host IPv6 no válido: {$field}.");
            }
        } elseif (!filter_var($host, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) {
            self::domain($host, $field);
            if (preg_match('/^[0-9.]+$/D', $host)) {
                throw new HttpError(422, "Dirección IP no válida: {$field}.");
            }
        }
        $port = $parsed['port'] ?? null;
        if ($port !== null && ($port < 1 || $port > 65535)) {
            throw new HttpError(422, "Puerto no válido: {$field}.");
        }
        return $scheme . '://' . $host . ($port !== null ? ':' . $port : '') . ($parsed['path'] ?? '') . (isset($parsed['query']) ? '?' . $parsed['query'] : '');
    }

    public static function customIcon(mixed $value): string
    {
        $value = self::text($value, 'customIcon', 131072);
        if ($value === '') {
            return '';
        }
        if (str_starts_with($value, 'data:')) {
            if (!preg_match('~^data:image/(?:png|jpeg|gif|webp);base64,[A-Za-z0-9+/]+={0,2}$~D', $value)) {
                throw new HttpError(422, 'El icono debe ser una imagen PNG, JPEG, GIF o WebP.');
            }
            return $value;
        }
        if (preg_match('~^https?://~i', $value)) {
            return self::url($value, 'customIcon');
        }
        return self::text($value, 'customIcon', 128);
    }

    public static function inventory(array $input): array
    {
        $result = [];
        foreach (['services', 'ips', 'categories'] as $field) {
            if (!isset($input[$field]) || !is_array($input[$field]) || !array_is_list($input[$field]) || count($input[$field]) > 2000) {
                throw new HttpError(422, "{$field} debe ser una lista de hasta 2000 elementos.");
            }
            $result[$field] = [];
            $ids = [];
            foreach ($input[$field] as $item) {
                if (!is_array($item) || array_is_list($item)) {
                    throw new HttpError(422, "Elemento no válido en {$field}.");
                }
                $id = self::text($item['id'] ?? null, 'id', 100, true);
                if (!preg_match('/^[A-Za-z0-9_-]+$/D', $id) || isset($ids[$id])) {
                    throw new HttpError(422, "ID no válido o duplicado en {$field}.");
                }
                $ids[$id] = true;
                if ($field === 'categories') {
                    $color = self::text($item['color'] ?? '#38bdf8', 'color', 7);
                    if (!preg_match('/^#[0-9a-f]{6}$/iD', $color)) {
                        throw new HttpError(422, 'Color no válido. Usa #RRGGBB.');
                    }
                    $result[$field][] = ['id' => $id, 'name' => self::text($item['name'] ?? null, 'name', 120, true), 'color' => $color];
                    continue;
                }
                $sshUser = self::text($item['sshUser'] ?? 'root', 'sshUser', 33);
                if ($sshUser !== '' && !preg_match('/^[a-z_][a-z0-9_-]{0,31}\$?$/D', $sshUser)) {
                    throw new HttpError(422, 'Usuario SSH no válido.');
                }
                $sshEnabled = $item['sshEnabled'] ?? true;
                if (!is_bool($sshEnabled)) {
                    throw new HttpError(422, 'sshEnabled debe ser booleano.');
                }
                $mac = strtoupper(self::text($item['mac'] ?? '', 'mac', 17));
                if ($mac !== '' && !preg_match('/^(?:[0-9A-F]{2}[:-]){5}[0-9A-F]{2}$/D', $mac)) {
                    throw new HttpError(422, 'Dirección MAC no válida.');
                }
                $record = ['id' => $id, 'mac' => str_replace('-', ':', $mac), 'sshEnabled' => $sshEnabled, 'sshUser' => $sshUser, 'notes' => self::text($item['notes'] ?? '', 'notes', 8192)];
                if ($field === 'services') {
                    $mode = $item['proxyMode'] ?? 'redirect';
                    if (!in_array($mode, ['dns_only', 'redirect'], true)) {
                        throw new HttpError(422, 'El tipo debe ser redirect o dns_only.');
                    }
                    $icon = self::text($item['icon'] ?? 'server', 'icon', 64);
                    if (!preg_match('/^[a-zA-Z0-9_-]*$/D', $icon)) {
                        throw new HttpError(422, 'Icono no válido.');
                    }
                    $url = $mode === 'redirect' ? self::url($item['url'] ?? null) : '';
                    if ($url !== '' && isset(parse_url($url)['query'])) {
                        throw new HttpError(422, 'Usa una URL base sin query.');
                    }
                    $record += ['name' => self::text($item['name'] ?? null, 'name', 160, true), 'url' => $url, 'ip' => $mode === 'dns_only' ? self::ip($item['ip'] ?? null) : '', 'domain' => self::domain($item['domain'] ?? '', 'domain', true), 'proxyMode' => $mode, 'category' => self::text($item['category'] ?? 'Servidores', 'category', 120), 'description' => self::text($item['description'] ?? '', 'description', 2048), 'icon' => $icon, 'customIcon' => self::customIcon($item['customIcon'] ?? '')];
                } else {
                    $record += ['ip' => self::directoryAddress($item['ip'] ?? null), 'hostname' => self::text($item['hostname'] ?? '', 'hostname', 253), 'tag' => self::text($item['tag'] ?? 'Servidores', 'tag', 120)];
                }
                $result[$field][] = $record;
            }
        }
        self::unique($result['services'], 'domain', true);
        self::unique($result['ips'], 'ip');
        self::unique($result['categories'], 'name');
        if ($result['categories'] === []) throw new HttpError(422, 'Debes conservar al menos una categoría.');
        $categoryNames = array_column($result['categories'], 'name');
        foreach ($result['services'] as $service) {
            if (!in_array($service['category'], $categoryNames, true)) throw new HttpError(422, 'La categoría de un servicio no existe: ' . $service['category']);
        }
        foreach ($result['ips'] as $host) {
            if (!in_array($host['tag'], $categoryNames, true)) throw new HttpError(422, 'La categoría de un equipo no existe: ' . $host['tag']);
        }
        return $result;
    }

    private static function unique(array $items, string $field, bool $skipEmpty = false): void
    {
        $seen = [];
        foreach ($items as $item) {
            $value = strtolower($item[$field]);
            if ($skipEmpty && $value === '') {
                continue;
            }
            if (isset($seen[$value])) {
                throw new HttpError(422, "Valor duplicado: {$field}.");
            }
            $seen[$value] = true;
        }
    }

    public static function cidrs(mixed $input): array
    {
        if (!is_array($input) || !array_is_list($input) || count($input) < 1 || count($input) > 32) {
            throw new HttpError(422, 'scanAllowedCidrs debe ser una lista de redes privadas.');
        }
        foreach ($input as $cidr) {
            if (!is_string($cidr) || !preg_match('~^([0-9.]+)/([0-9]{1,2})$~D', $cidr, $match)) {
                throw new HttpError(422, 'Red de escaneo no válida.');
            }
            $ip = self::ip($match[1]);
            $prefix = (int) $match[2];
            if ($prefix > 32 || !NetworkScanner::isPrivate($ip) || !NetworkScanner::cidrIsPrivate($ip, $prefix)) {
                throw new HttpError(422, 'Solo se permiten subredes RFC1918.');
            }
        }
        return array_values(array_unique($input));
    }
}
