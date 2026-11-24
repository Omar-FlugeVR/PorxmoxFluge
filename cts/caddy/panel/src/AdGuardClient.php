<?php
declare(strict_types=1);
namespace Homelab;

final class AdGuardClient
{
    private \Closure $transport;

    public function __construct(private array $config, ?callable $transport = null)
    {
        $this->transport = $transport !== null ? \Closure::fromCallable($transport) : $this->curl(...);
    }

    private function curl(string $path, string $method, ?array $payload): array
    {
        $url = rtrim(Validator::url($this->config['adguardUrl'], 'adguardUrl'), '/') . $path;
        $curl = curl_init($url);
        curl_setopt_array($curl, [CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 5, CURLOPT_CONNECTTIMEOUT => 2, CURLOPT_USERPWD => $this->config['adguardUser'] . ':' . $this->config['adguardPass'], CURLOPT_HTTPAUTH => CURLAUTH_BASIC, CURLOPT_PROTOCOLS => CURLPROTO_HTTP | CURLPROTO_HTTPS, CURLOPT_FOLLOWLOCATION => false]);
        if ($method === 'POST') {
            curl_setopt_array($curl, [CURLOPT_POST => true, CURLOPT_POSTFIELDS => json_encode($payload, JSON_THROW_ON_ERROR), CURLOPT_HTTPHEADER => ['Content-Type: application/json']]);
        }
        $body = curl_exec($curl);
        $status = curl_getinfo($curl, CURLINFO_HTTP_CODE);
        $failed = $body === false;
        curl_close($curl);
        return ['status' => $status, 'body' => $body, 'failed' => $failed];
    }

    private function request(string $path, string $method, ?array $payload = null): mixed
    {
        $response = ($this->transport)($path, $method, $payload);
        if (($response['failed'] ?? false) || ($response['status'] ?? 0) < 200 || $response['status'] >= 300) {
            throw new HttpError(502, 'AdGuard no confirmó la operación ' . basename($path) . ' (HTTP ' . ($response['status'] ?? 0) . ').');
        }
        return $response['body'] ?? '';
    }

    public function list(): array
    {
        try {
            $settings = json_decode($this->request('/control/rewrite/settings', 'GET'), true, 32, JSON_THROW_ON_ERROR);
        } catch (\JsonException $error) {
            throw new HttpError(502, 'AdGuard devolvió ajustes de reescritura no válidos.');
        }
        if (($settings['enabled'] ?? false) !== true) {
            throw new HttpError(502, 'Las reescrituras DNS están desactivadas en AdGuard. Actívalas antes de aplicar los accesos.');
        }
        $body = $this->request('/control/rewrite/list', 'GET');
        try {
            $entries = json_decode($body, true, 32, JSON_THROW_ON_ERROR);
        } catch (\JsonException $error) {
            throw new HttpError(502, 'AdGuard devolvió una lista DNS no válida.');
        }
        if (!is_array($entries) || !array_is_list($entries)) {
            throw new HttpError(502, 'AdGuard devolvió una lista DNS no válida.');
        }
        return $entries;
    }

    public function add(string $domain, string $answer): void
    {
        $this->request('/control/rewrite/add', 'POST', compact('domain', 'answer'));
    }

    public function delete(string $domain, string $answer): void
    {
        $this->request('/control/rewrite/delete', 'POST', compact('domain', 'answer'));
    }
}
