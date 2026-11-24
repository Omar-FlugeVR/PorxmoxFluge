<?php
declare(strict_types=1);
namespace Homelab;

final class CaddyRenderer
{
    public static function plan(array $services, array $config): array
    {
        $panel = Validator::domain($config['dashboardDomain']);
        $server = Validator::ip($config['serverProxyIp']);
        $base = Validator::domain($config['baseDomain']);
        $dns = [$panel => $server];
        $routes = [];
        $seen = [$panel => true];
        foreach ($services as $service) {
            $domain = Validator::domain($service['domain'] ?? '', 'domain', true);
            if ($domain === '') continue;
            $subdomain = substr($domain, 0, -strlen('.' . $base));
            if (!str_ends_with($domain, '.' . $base) || str_contains($subdomain, '.') || isset($seen[$domain])) {
                throw new HttpError(422, 'Dominio duplicado, reservado o fuera de .' . $base . ': ' . $domain);
            }
            $seen[$domain] = true;
        }
        foreach ($services as $service) {
            $domain = $service['domain'] ?? '';
            if ($domain === '') continue;
            if (($service['proxyMode'] ?? 'redirect') === 'dns_only') {
                $ip = Validator::ip($service['ip'] ?? '');
                if (!NetworkScanner::isPrivate($ip)) throw new HttpError(422, 'DNS directo requiere una IP privada.');
                $dns[$domain] = $ip;
            } else {
                $target = Validator::url($service['url']);
                $host = strtolower(parse_url($target, PHP_URL_HOST));
                if ($host === $server || isset($seen[$host])) throw new HttpError(422, 'La URL crea un bucle con el panel o sus accesos.');
                $dns[$domain] = $server;
                $routes[] = compact('domain', 'target');
            }
        }
        return ['candidate' => ['version' => 1, 'routes' => $routes], 'desiredDns' => $dns];
    }

    public static function renderCandidate(array $candidate, array $reserved = []): string
    {
        if (($candidate['version'] ?? null) !== 1 || !isset($candidate['routes']) || !is_array($candidate['routes']) || !array_is_list($candidate['routes']) || count($candidate['routes']) > 2000) {
            throw new HttpError(422, 'Plan de Caddy no válido.');
        }
        $result = "# Gestionado por Servicio de control.\n";
        $seen = array_fill_keys($reserved, true);
        foreach ($candidate['routes'] as $route) {
            if (!is_array($route)) throw new HttpError(422, 'Ruta no válida.');
            $domain = Validator::domain($route['domain'] ?? null);
            $target = Validator::url($route['target'] ?? null);
            if (isset($seen[$domain]) || isset(parse_url($target)['query'])) throw new HttpError(422, 'Dominio reservado, duplicado o destino no válido.');
            $seen[$domain] = true;
            $target = rtrim($target, '/');
            $result .= "http://{$domain} {\n\tredir \"{$target}{uri}\" 302\n}\n\n";
        }
        return $result;
    }
}
