<?php
declare(strict_types=1);
namespace Homelab;

/** Shared scanner preferences describe the UI; they never configure a DHCP server. */
final class ScannerSettings
{
    public const LIMIT = 65536;

    public static function defaults(): array
    {
        return ['scanPresets' => [], 'dhcpRange' => ['start' => '192.168.1.100', 'end' => '192.168.1.250']];
    }

    private static function networkBounds(string $cidr): array
    {
        Validator::cidrs([$cidr]);
        [$base, $prefix] = explode('/', $cidr);
        $prefix = (int) $prefix;
        $size = 1 << (32 - $prefix);
        $first = intdiv(ip2long($base), $size) * $size;
        return ['cidr' => long2ip($first) . '/' . $prefix, 'first' => $first,
            'last' => $first + $size - 1, 'prefix' => $prefix];
    }

    public static function validateNetworks(mixed $networks): array
    {
        if (!is_array($networks) || !array_is_list($networks) || count($networks) < 1 || count($networks) > 32) {
            throw new HttpError(422, 'Configura entre 1 y 32 redes.');
        }
        $result = []; $ids = []; $cidrs = []; $names = [];
        foreach ($networks as $network) {
            if (!is_array($network)) throw new HttpError(422, 'La configuración de una red no es válida.');
            $id = Validator::text($network['id'] ?? null, 'id', 64, true);
            if (!preg_match('/^[A-Za-z0-9_-]{1,64}$/D', $id) || isset($ids[$id])) throw new HttpError(422, 'Cada red necesita un identificador único.');
            $name = Validator::text($network['name'] ?? null, 'name', 320, true);
            if (preg_match_all('/./us', $name) > 80 || preg_match('/[\r\n\t]/', $name)) throw new HttpError(422, 'El nombre de la red admite hasta 80 caracteres en una sola línea.');
            foreach ($names as $previous) if (preg_match('/^' . preg_quote($name, '/') . '$/iuD', $previous)) throw new HttpError(422, 'Ya existe una red con ese nombre.');
            $bounds = self::networkBounds(Validator::text($network['cidr'] ?? null, 'cidr', 32, true));
            $cidr = $bounds['cidr'];
            if (isset($cidrs[$cidr])) throw new HttpError(422, 'La red ' . $cidr . ' ya está configurada.');
            $pool = $network['dhcpPool'] ?? null;
            if ($pool !== null) {
                if (!is_array($pool) || !isset($pool['start'], $pool['end'])) throw new HttpError(422, 'El pool DHCP necesita una dirección inicial y una final.');
                $start = Validator::ip($pool['start'], 'dhcpPool.start');
                $end = Validator::ip($pool['end'], 'dhcpPool.end');
                $first = $bounds['first'] + ($bounds['prefix'] <= 30 ? 1 : 0);
                $last = $bounds['last'] - ($bounds['prefix'] <= 30 ? 1 : 0);
                if (ip2long($start) > ip2long($end) || ip2long($start) < $first || ip2long($end) > $last) throw new HttpError(422, 'El pool DHCP debe estar ordenado y dentro de las IP utilizables de ' . $cidr . '.');
                $pool = compact('start', 'end');
            }
            $ids[$id] = $cidrs[$cidr] = true; $names[] = $name;
            $result[] = ['id' => $id, 'name' => $name, 'cidr' => $cidr, 'dhcpPool' => $pool];
        }
        return $result;
    }

    /** Upgrade the old allowlist and shared DHCP interval without writing on a read. */
    public static function fromLegacy(array $config): array
    {
        $networks = []; $seen = []; $names = [];
        $pool = $config['dhcpRange'] ?? null;
        foreach ($config['scanAllowedCidrs'] as $cidr) {
            $bounds = self::networkBounds($cidr);
            if (isset($seen[$bounds['cidr']])) continue;
            $seen[$bounds['cidr']] = true;
            $name = 'Red ' . $bounds['cidr'];
            foreach ($config['scanPresets'] ?? [] as $preset) {
                if (preg_match('/^([0-9.]+)/', $preset['range'] ?? '', $match)
                    && filter_var($match[1], FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)
                    && NetworkScanner::inCidr($match[1], $bounds['cidr'])) { $name = $preset['label']; break; }
            }
            foreach ($names as $previous) if (preg_match('/^' . preg_quote($name, '/') . '$/iuD', $previous)) { $name = 'Red ' . $bounds['cidr']; break; }
            $names[] = $name;
            $dhcpPool = null;
            if (is_array($pool) && isset($pool['start'], $pool['end'])) {
                $start = max(ip2long(Validator::ip($pool['start'])), $bounds['first'] + ($bounds['prefix'] <= 30 ? 1 : 0));
                $end = min(ip2long(Validator::ip($pool['end'])), $bounds['last'] - ($bounds['prefix'] <= 30 ? 1 : 0));
                if ($start <= $end) $dhcpPool = ['start' => long2ip($start), 'end' => long2ip($end)];
            }
            $networks[] = ['id' => 'network-' . count($networks), 'name' => $name, 'cidr' => $bounds['cidr'], 'dhcpPool' => $dhcpPool];
        }
        return self::validateNetworks($networks);
    }

    public static function configuration(mixed $networks): array
    {
        $networks = self::validateNetworks($networks);
        $pools = array_values(array_filter(array_column($networks, 'dhcpPool')));
        return ['scanNetworks' => $networks, 'scanAllowedCidrs' => array_column($networks, 'cidr'),
            'scanPresets' => array_map(fn($network) => ['id' => $network['id'], 'label' => $network['name'], 'range' => $network['cidr']], $networks),
            'dhcpRange' => $pools[0] ?? null];
    }

    private static function allowed(string $ip, array $cidrs): void
    {
        if (NetworkScanner::isPrivate($ip)) {
            foreach ($cidrs as $cidr) {
                if (NetworkScanner::inCidr($ip, $cidr)) {
                    return;
                }
            }
        }
        throw new HttpError(422, 'El rango contiene direcciones que no pertenecen a las redes privadas permitidas.');
    }

    /** Same bounded IPv4/range/CIDR/comma grammar as the browser scanner. */
    public static function parseRange(mixed $input, array $allowedCidrs): array
    {
        $input = Validator::text($input, 'range', 8192, true);
        $allowedCidrs = Validator::cidrs($allowedCidrs);
        $ips = [];
        $append = static function (int $start, int $end) use (&$ips, $allowedCidrs): void {
            $first = min($start, $end);
            $last = max($start, $end);
            if ($last - $first + 1 > self::LIMIT) {
                throw new HttpError(422, 'Cada subred de escaneo admite como máximo ' . self::LIMIT . ' direcciones (/16).');
            }
            for ($value = $first; $value <= $last; ++$value) {
                $ip = long2ip($value);
                self::allowed($ip, $allowedCidrs);
                $ips[$ip] = true;
                if (count($ips) > self::LIMIT) {
                    throw new HttpError(422, 'Cada subred de escaneo admite como máximo ' . self::LIMIT . ' direcciones (/16).');
                }
            }
        };
        foreach (preg_split('/[,;]/', $input) as $part) {
            $part = trim($part);
            if ($part === '') {
                continue;
            }
            if (str_contains($part, '/')) {
                $parts = explode('/', $part);
                if (count($parts) !== 2 || !preg_match('/^[0-9]{1,2}$/D', $parts[1]) || (int) $parts[1] < 16 || (int) $parts[1] > 32) {
                    throw new HttpError(422, 'Introduce una subred IPv4 válida, por ejemplo 10.0.10.0/24.');
                }
                $base = ip2long(Validator::ip($parts[0]));
                $prefix = (int) $parts[1];
                $count = 1 << (32 - $prefix);
                $network = intdiv($base, $count) * $count;
                $append($network + ($prefix <= 30 ? 1 : 0), $network + $count - ($prefix <= 30 ? 2 : 1));
            } elseif (str_contains($part, '-')) {
                $parts = array_map('trim', explode('-', $part));
                if (count($parts) !== 2) {
                    throw new HttpError(422, 'Introduce un rango IPv4 válido, por ejemplo 10.0.10.1-254.');
                }
                $start = Validator::ip($parts[0]);
                $end = str_contains($parts[1], '.') ? $parts[1] : substr($start, 0, strrpos($start, '.') + 1) . $parts[1];
                $append(ip2long($start), ip2long(Validator::ip($end)));
            } else {
                $value = ip2long(Validator::ip($part));
                $append($value, $value);
            }
        }
        if ($ips === []) {
            throw new HttpError(422, 'La subred debe contener al menos una dirección IPv4.');
        }
        return array_keys($ips);
    }

    public static function validate(mixed $presets, mixed $dhcp, mixed $allowed): array
    {
        $allowed = Validator::cidrs($allowed);
        if (!is_array($presets) || !array_is_list($presets) || count($presets) > 32) {
            throw new HttpError(422, 'Configura como máximo 32 subredes de acceso rápido.');
        }
        $validated = [];
        $seen = [];
        foreach ($presets as $preset) {
            if (!is_array($preset)) {
                throw new HttpError(422, 'La configuración de una subred no es válida.');
            }
            $id = Validator::text($preset['id'] ?? null, 'id', 64, true);
            if (!preg_match('/^[A-Za-z0-9_-]{1,64}$/D', $id) || isset($seen[$id])) {
                throw new HttpError(422, 'Cada subred debe tener un identificador válido y único.');
            }
            $seen[$id] = true;
            $label = Validator::text($preset['label'] ?? null, 'label', 320, true);
            $length = preg_match_all('/./us', $label);
            if ($length === false || $length > 80 || preg_match('/[\x00-\x1f\x7f<>]/u', $label)) {
                throw new HttpError(422, 'El nombre de cada subred debe ser texto de hasta 80 caracteres.');
            }
            $range = Validator::text($preset['range'] ?? null, 'range', 8192, true);
            self::parseRange($range, $allowed);
            $validated[] = compact('id', 'label', 'range');
        }
        if (!is_array($dhcp) || array_is_list($dhcp) || !array_key_exists('start', $dhcp) || !array_key_exists('end', $dhcp)) {
            throw new HttpError(422, 'El rango DHCP debe incluir una dirección inicial y una final.');
        }
        $start = Validator::ip($dhcp['start'], 'dhcpRange.start');
        $end = Validator::ip($dhcp['end'], 'dhcpRange.end');
        if (ip2long($start) > ip2long($end)) {
            throw new HttpError(422, 'El inicio del rango DHCP debe ser anterior o igual al final.');
        }
        self::allowed($start, $allowed);
        self::allowed($end, $allowed);
        // Check interval coverage without enumerating a potentially large DHCP range.
        $intervals = [];
        foreach ($allowed as $cidr) {
            [$base, $prefix] = explode('/', $cidr);
            $size = 1 << (32 - (int) $prefix);
            $first = intdiv(ip2long($base), $size) * $size;
            $intervals[] = [$first, $first + $size - 1];
        }
        usort($intervals, static fn(array $a, array $b): int => $a[0] <=> $b[0]);
        $cursor = ip2long($start);
        foreach ($intervals as [$first, $last]) {
            if ($first > $cursor) {
                break;
            }
            if ($last >= $cursor) {
                $cursor = $last + 1;
            }
            if ($cursor > ip2long($end)) {
                return ['scanPresets' => $validated, 'dhcpRange' => compact('start', 'end'), 'scanAllowedCidrs' => $allowed];
            }
        }
        throw new HttpError(422, 'Todo el rango DHCP debe pertenecer a las redes privadas permitidas.');
    }
}
