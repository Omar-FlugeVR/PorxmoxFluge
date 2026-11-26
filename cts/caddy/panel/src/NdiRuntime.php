<?php
declare(strict_types=1);
namespace Homelab;

final class NdiRuntime
{
    private const DEMO_PACKAGE = 'dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd';

    public function __construct(private JsonStore $store) {}

    public function prepare(): array
    {
        if (NdiMonitor::mock()) {
            if ($this->store->read('ndi-runtime-demo.json')['installed'] ?? false) return ['installed' => true];
            return ['installed' => false, 'package' => self::DEMO_PACKAGE,
                'license' => 'Licencia de la muestra. Esta operación simula la instalación del runtime NDI.'];
        }
        $result = $this->run(['action' => 'prepare']);
        return $result['installed'] ? $this->installed() : $result;
    }

    public function install(array $input): array
    {
        $package = $input['package'] ?? '';
        if (($input['accepted'] ?? false) !== true || !is_string($package) || !preg_match('/^[a-f0-9]{64}$/D', $package)) {
            throw new HttpError(422, 'Confirma la licencia del paquete antes de instalar el runtime.');
        }
        if (NdiMonitor::mock() && $package !== self::DEMO_PACKAGE) {
            throw new HttpError(409, 'El paquete ha cambiado. Abre de nuevo la licencia de NDI.');
        }
        if (!NdiMonitor::mock()) {
            $this->run(['action' => 'install', 'accepted' => true, 'package' => $package]);
        }
        return $this->installed();
    }

    private function installed(): array
    {
        return $this->store->locked('ndi', function (): array {
            if (NdiMonitor::mock()) $this->store->write('ndi-runtime-demo.json', ['installed' => true]);
            $cache = $this->store->read('ndi-cache.json');
            if ($cache) {
                unset($cache['key']);
                $this->store->write('ndi-cache.json', $cache);
            }
            return ['installed' => true];
        });
    }

    private function run(array $input): array
    {
        $helper = '/opt/fluge-control/deploy/install-ndi-runtime.py';
        $process = proc_open(['/usr/bin/sudo', '-n', $helper], [['pipe','r'], ['pipe','w'], ['file','/dev/null','a']], $pipes);
        if (!is_resource($process)) throw new HttpError(503, 'El instalador del runtime NDI no está disponible.');
        fwrite($pipes[0], json_encode($input, JSON_THROW_ON_ERROR));
        fclose($pipes[0]);
        $output = stream_get_contents($pipes[1], 2 * 1024 * 1024);
        fclose($pipes[1]);
        $code = proc_close($process);
        $result = json_decode($output === false ? '' : $output, true);
        if ($code !== 0 || !is_array($result) || !isset($result['installed'])) {
            throw new HttpError(503, $result['error'] ?? 'El instalador del runtime NDI no está disponible.');
        }
        return $result;
    }
}
