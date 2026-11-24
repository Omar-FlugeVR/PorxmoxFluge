<?php
declare(strict_types=1);
namespace Homelab;

final class InfrastructureSynchronizer
{
    public function __construct(private JsonStore $store, private InventoryRepository $inventory, private ConfigRepository $config) {}

    public function plan(): array
    {
        $inventory = $this->inventory->get();
        $config = $this->config->get();
        return CaddyRenderer::plan($inventory['services'], $config) + ['revision' => $inventory['revision'], 'configRevision' => $config['configRevision']];
    }

    /** A publication failure must not turn a completed save into a failed save. */
    public function afterSave(int $revision): array
    {
        try {
            return $this->sync($revision);
        } catch (\Throwable $error) {
            error_log('Automatic publication: ' . get_class($error) . ': ' . $error->getMessage());
            return ['ok' => false, 'caddy_updated' => null, 'revision' => $revision,
                'demo' => getenv('FLUGE_DEMO') === '1', 'at' => gmdate('c'),
                'errors' => [$error instanceof HttpError ? $error->getMessage() : 'No se pudo confirmar la publicación de los accesos.']];
        }
    }

    public function sync(int $expectedRevision): array
    {
        return $this->store->locked('infrastructure', fn() => $this->store->locked('config', fn() => $this->store->locked('inventory', function () use ($expectedRevision): array {
            $inventory = $this->inventory->get();
            if ($inventory['revision'] !== $expectedRevision) throw new HttpError(409, 'El inventario ha cambiado. Recarga antes de aplicar.');
            $config = $this->config->get();
            $plan = CaddyRenderer::plan($inventory['services'], $config);
            $compiled = CaddyRenderer::renderCandidate($plan['candidate'], [$config['dashboardDomain']]);
            $demo = getenv('FLUGE_DEMO') === '1';
            $routesOk = false;
            if ($demo) {
                $this->store->write('demo-routes.json', ['content' => $compiled]);
                $routesOk = true;
            } else {
                $this->store->write('caddy-candidate.json', $plan['candidate']);
                exec('sudo -n /usr/local/sbin/fluge-control-apply-routes 2>&1', $output, $code);
                $routesOk = $code === 0;
                if (!$routesOk) error_log('Caddy: ' . implode("\n", $output));
            }
            if (!$routesOk) {
                $result = ['ok' => false, 'caddy_updated' => false, 'errors' => ['Caddy rechazó las rutas. Se conserva la configuración anterior.']];
            } else {
                $transport = $demo ? function (string $path, string $method, ?array $payload): array {
                    if (str_ends_with($path, '/settings')) return ['status' => 200, 'body' => json_encode($this->store->read('demo-dns-settings.json', ['enabled' => true]))];
                    $entries = $this->store->read('demo-dns.json');
                    if ($method === 'GET') return ['status' => 200, 'body' => json_encode($entries)];
                    if (str_ends_with($path, '/add')) $entries[] = $payload;
                    else $entries = array_values(array_filter($entries, fn($item) => $item !== $payload));
                    $this->store->write('demo-dns.json', $entries);
                    return ['status' => 200, 'body' => 'OK'];
                } : null;
                try {
                    if (!$demo && ($config['adguardUrl'] === '' || $config['adguardUser'] === '' || $config['adguardPass'] === '')) throw new HttpError(422, 'Configura la conexión con AdGuard en Configuración.');
                    $result = (new DnsSynchronizer($this->store))->sync($plan['desiredDns'], new AdGuardClient($config, $transport)) + ['caddy_updated' => true];
                } catch (HttpError $error) {
                    $result = ['ok' => false, 'caddy_updated' => true, 'errors' => [$error->getMessage()]];
                }
            }
            $result += ['revision' => $inventory['revision'], 'configRevision' => $config['configRevision'], 'demo' => $demo, 'at' => gmdate('c')];
            $this->store->write('last-sync.json', $result);
            return $result;
        })));
    }
}
