<?php
declare(strict_types=1);
namespace Homelab;

final class NetworkScanner
{
    private const PRIVATE_CIDRS = ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'];
    private const PORTS = [80, 443, 22, 8080, 8000, 8006, 53, 445, 8123, 5959];

    public static function inCidr(string $ip, string $cidr): bool
    {
        [$base, $prefix] = explode('/', $cidr);
        $prefix = (int) $prefix;
        $mask = $prefix === 0 ? 0 : ((0xffffffff << (32 - $prefix)) & 0xffffffff);
        return (ip2long($ip) & $mask) === (ip2long($base) & $mask);
    }

    public static function isPrivate(string $ip): bool
    {
        foreach (self::PRIVATE_CIDRS as $cidr) {
            if (self::inCidr($ip, $cidr)) {
                return true;
            }
        }
        return false;
    }

    public static function cidrIsPrivate(string $ip, int $prefix): bool
    {
        foreach (self::PRIVATE_CIDRS as $cidr) {
            [, $privatePrefix] = explode('/', $cidr);
            if ($prefix >= (int) $privatePrefix && self::inCidr($ip, $cidr)) {
                return true;
            }
        }
        return false;
    }

    public static function validateTargets(mixed $ips, array $allowedCidrs): array
    {
        Validator::cidrs($allowedCidrs);
        if (!is_array($ips) || !array_is_list($ips) || count($ips) < 1 || count($ips) > 32) {
            throw new HttpError(422, 'Cada bloque debe contener entre 1 y 32 direcciones IPv4.');
        }
        $result = [];
        foreach ($ips as $ip) {
            $ip = Validator::ip($ip);
            $allowed = false;
            foreach ($allowedCidrs as $cidr) {
                $allowed = $allowed || self::inCidr($ip, $cidr);
            }
            if (!self::isPrivate($ip) || !$allowed) {
                throw new HttpError(422, 'La dirección ' . $ip . ' no pertenece a una red privada permitida.');
            }
            $result[] = $ip;
        }
        return array_values(array_unique($result));
    }

    public function scan(mixed $requestedIps, array $allowedCidrs): array
    {
        $ips = self::validateTargets($requestedIps, $allowedCidrs);
        $started = microtime(true);
        $sockets = [];
        $ports = [];
        $latencies = [];
        foreach ($ips as $ip) {
            foreach (self::PORTS as $port) {
                $socket = @stream_socket_client('tcp://' . $ip . ':' . $port, $errno, $error, 0, STREAM_CLIENT_ASYNC_CONNECT);
                if ($socket !== false) {
                    stream_set_blocking($socket, false);
                    $sockets[(int) $socket] = ['socket' => $socket, 'ip' => $ip, 'port' => $port];
                }
            }
        }
        try {
            while ($sockets !== [] && microtime(true) - $started < 0.35) {
                $write = array_column($sockets, 'socket');
                $read = null;
                $except = null;
                $changed = @stream_select($read, $write, $except, 0, 40000);
                if ($changed === false) {
                    break;
                }
                foreach ($write as $socket) {
                    $key = (int) $socket;
                    $entry = $sockets[$key];
                    if (@stream_socket_get_name($socket, true) !== false) {
                        $ports[$entry['ip']][] = $entry['port'];
                        $latencies[$entry['ip']] ??= round((microtime(true) - $started) * 1000, 1);
                    }
                    fclose($socket);
                    unset($sockets[$key]);
                }
            }
        } finally {
            foreach ($sockets as $entry) {
                fclose($entry['socket']);
            }
        }
        usleep(25000);
        $arp = $this->arp();
        $hosts = $this->hosts();
        $results = [];
        foreach ($ips as $ip) {
            $alive = isset($ports[$ip]) || isset($arp[$ip]);
            $hostname = $hosts[$ip] ?? '';
            if ($alive && $hostname === '') {
                $hostname = $this->netbiosName($ip);
            }
            $results[] = ['ip' => $ip, 'isAlive' => $alive, 'hostname' => $hostname, 'mac' => $arp[$ip] ?? '', 'ports' => $ports[$ip] ?? [], 'latency' => $latencies[$ip] ?? null];
        }
        return ['elapsed' => round(microtime(true) - $started, 3), 'count' => count($results), 'results' => $results];
    }

    private function arp(): array
    {
        $result = [];
        foreach (is_readable('/proc/net/arp') ? file('/proc/net/arp') : [] as $line) {
            $parts = preg_split('/\s+/', trim($line));
            if (count($parts) >= 4 && $parts[2] === '0x2' && $parts[3] !== '00:00:00:00:00:00') {
                $result[$parts[0]] = strtoupper($parts[3]);
            }
        }
        return $result;
    }

    private function hosts(): array
    {
        $result = [];
        foreach (is_readable('/etc/hosts') ? file('/etc/hosts') : [] as $line) {
            $parts = preg_split('/\s+/', trim(preg_replace('/#.*/', '', $line)));
            if (count($parts) >= 2 && filter_var($parts[0], FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) {
                $result[$parts[0]] = $parts[1];
            }
        }
        return $result;
    }

    private function netbiosName(string $ip): string
    {
        $socket = @fsockopen('udp://' . $ip, 137, $errno, $error, 0.05);
        if ($socket === false) {
            return '';
        }
        stream_set_timeout($socket, 0, 50000);
        $packet = "\x80\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x20\x43\x4b" . str_repeat("\x41", 30) . "\x00\x00\x21\x00\x01";
        fwrite($socket, $packet);
        $response = @fread($socket, 512);
        fclose($socket);
        if ($response !== false && strlen($response) > 56 && ord($response[56]) > 0) {
            $name = trim(substr($response, 57, 15));
            return preg_match('/^[a-zA-Z0-9_-]+$/D', $name) ? $name : '';
        }
        return '';
    }
}
