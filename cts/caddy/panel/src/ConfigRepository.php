<?php
declare(strict_types=1);
namespace Homelab;

final class ConfigRepository
{
    public function __construct(private JsonStore $store) {}

    public function get(): array
    {
        $config = $this->store->read('config.json') + ['installationName' => '', 'adguardUrl' => '', 'adguardUser' => '', 'adguardPass' => '', 'dashboardDomain' => 'inicio.lan', 'serverProxyIp' => '192.168.1.12', 'baseDomain' => 'lan', 'configRevision' => 0, 'scanAllowedCidrs' => ['192.168.1.0/24']] + ScannerSettings::defaults();
        $config['ndi'] = NdiSettings::validate($config['ndi'] ?? NdiSettings::defaults());
        return array_replace($config, ScannerSettings::configuration($config['scanNetworks'] ?? ScannerSettings::fromLegacy($config)));
    }

    public function publicConfig(): array
    {
        $config = $this->get();
        return array_intersect_key($config, array_flip(['installationName', 'adguardUrl', 'adguardUser', 'dashboardDomain', 'serverProxyIp', 'baseDomain', 'configRevision', 'scanNetworks', 'scanAllowedCidrs', 'scanPresets', 'dhcpRange', 'ndi'])) + ['hasPassword' => $config['adguardPass'] !== ''];
    }

    public function scannerConfig(): array
    {
        return array_intersect_key($this->get(), array_flip(['scanNetworks', 'scanPresets', 'dhcpRange', 'scanAllowedCidrs', 'configRevision'])) + ['scanLimit' => ScannerSettings::LIMIT];
    }

    public function applyNdiDefault(array $input): void
    {
        $ndi = NdiSettings::validate($input);
        $this->store->locked('config', function () use ($ndi): void {
            if (!is_file($this->store->path('config.json'))) {
                throw new HttpError(503, 'Inicializa el panel antes de configurar NDI.');
            }
            $config = $this->store->read('config.json');
            $current = NdiSettings::validate($config['ndi'] ?? NdiSettings::defaults());
            $automatic = $config['ndiAutomatic'] ?? ($current == NdiSettings::defaults());
            if (!$automatic || $current == $ndi) {
                return;
            }
            // El destino automático es independiente de la revisión de publicación DNS.
            $config['ndi'] = $ndi;
            $config['ndiAutomatic'] = true;
            $this->store->write('config.json', $config);
        });
    }

    public function save(array $input): array
    {
        return $this->store->locked('accounts', fn(): array => $this->store->locked('config', function () use ($input): array {
            $config = $this->get();
            $previousUsername = $config['adguardUser'];
            $previousPassword = $config['adguardPass'];
            if (array_key_exists('ndi', $input)) {
                $config['ndi'] = NdiSettings::validate($input['ndi']);
                $config['ndiAutomatic'] = false;
            }
            if (Validator::revision($input['configRevision'] ?? null, 'configRevision') !== $config['configRevision']) {
                throw new HttpError(409, 'Los ajustes han cambiado en otra sesión.', ['configRevision' => $config['configRevision']]);
            }
            foreach (['installationName', 'adguardUrl', 'adguardUser', 'adguardPass', 'scanAllowedCidrs', 'scanPresets', 'dhcpRange'] as $field) {
                if (!array_key_exists($field, $input)) {
                    continue;
                }
                if ($field === 'installationName') {
                    $name = Validator::text($input[$field], $field, 320);
                    if (preg_match('/[\r\n\t]/', $name) || preg_match_all('/./us', $name) > 80) throw new HttpError(422, 'El nombre de la instalación debe tener hasta 80 caracteres en una sola línea.');
                    $config[$field] = $name;
                } elseif ($field === 'adguardUrl') {
                    $config[$field] = Validator::url($input[$field], $field);
                } elseif ($field === 'serverProxyIp') {
                    $config[$field] = Validator::ip($input[$field], $field);
                } elseif ($field === 'scanAllowedCidrs') {
                    $config[$field] = Validator::cidrs($input[$field]);
                } elseif (in_array($field, ['scanPresets', 'dhcpRange'], true)) {
                    $config[$field] = $input[$field];
                } elseif ($field === 'adguardPass') {
                    if (!is_string($input[$field]) || strlen($input[$field]) > 1024 || str_contains($input[$field], "\0")) {
                        throw new HttpError(422, 'Contraseña de AdGuard no válida.');
                    }
                    // An empty password means preserve the existing credential.
                    if ($input[$field] !== '') {
                        $config[$field] = $input[$field];
                    }
                } else {
                    $config[$field] = Validator::text($input[$field], $field, 128, true);
                }
            }
            $legacyNetworks = array_key_exists('scanPresets', $input) || array_key_exists('dhcpRange', $input) || array_key_exists('scanAllowedCidrs', $input);
            if (array_key_exists('scanNetworks', $input)) {
                if ($legacyNetworks) throw new HttpError(422, 'Envía las redes completas sin mezclar formatos de configuración.');
                $config = array_replace($config, ScannerSettings::configuration($input['scanNetworks']));
            } elseif ($legacyNetworks) {
                $legacy = ScannerSettings::validate($input['scanPresets'] ?? [], $config['dhcpRange'], $config['scanAllowedCidrs']);
                $config = array_replace($config, ScannerSettings::configuration(ScannerSettings::fromLegacy($legacy)));
            }
            if (($this->store->read('auth.json', ['mode' => 'adguard'])['mode'] ?? 'independent') === 'adguard'
                && ($config['adguardUser'] !== $previousUsername || $config['adguardPass'] !== $previousPassword)) {
                throw new HttpError(409, 'Actualiza primero la contraseña de la cuenta principal desde Cuentas para gestionar por separado la conexión con AdGuard.');
            }
            if ($config['adguardUser'] !== $previousUsername) {
                (new UsersRepository($this->store, $this))->assertAdGuardUsernameAvailable($config['adguardUser']);
            }
            ++$config['configRevision'];
            $this->store->write('config.json', $config);
            return $this->publicConfig();
        }));
    }
}
