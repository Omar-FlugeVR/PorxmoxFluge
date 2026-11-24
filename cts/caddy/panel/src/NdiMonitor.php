<?php
declare(strict_types=1);
namespace Homelab;

/** Request-driven cache: no daemon, timers or SDK instances survive a request. */
final class NdiMonitor
{
    public function __construct(private JsonStore $store, private ConfigRepository $config) {}

    public static function mock(): bool
    {
        return getenv('FLUGE_DEMO') === '1' || in_array(strtolower(getenv('NDI_MOCK') ?: ''), ['true','1'], true);
    }

    private function key(array $settings): string
    {
        return hash('sha256', json_encode(['history-v1',$settings,self::mock()], JSON_THROW_ON_ERROR));
    }

    public function status(): array
    {
        if (!$this->config->get()['ndi']['enabled']) return ['enabled' => false];
        return $this->store->locked('ndi', function (): array {
            $settings = $this->config->get()['ndi'];
            if (!$settings['enabled']) return ['enabled' => false];
            $key = $this->key($settings);
            $cache = $this->store->read('ndi-cache.json');
            if (($cache['key'] ?? '') === $key && microtime(true) - ($cache['checked_at'] ?? 0) < $settings['polling_rate']) return $cache['status'];
            $cancel = fn(): bool => $this->key($this->config->get()['ndi']) !== $key;
            $status = $this->probe($settings, $cancel, ($cache['sequence'] ?? 0) + 1);
            $current = $this->config->get()['ndi'];
            if (!$current['enabled']) return ['enabled' => false];
            if ($this->key($current) !== $key) return $this->base($current) + ['pending' => true, 'warning' => 'Los ajustes cambiaron durante la consulta. Se actualizará en el próximo ciclo.'];
            $status += (new NdiHistory($this->store))->record($settings, $status);
            $this->store->write('ndi-cache.json', ['key' => $key, 'checked_at' => microtime(true), 'sequence' => ($cache['sequence'] ?? 0) + 1, 'status' => $status]);
            return $status;
        });
    }

    public function clearLog(): array
    {
        return $this->store->locked('ndi', function (): array {
            $settings = $this->config->get()['ndi'];
            (new NdiHistory($this->store))->clearLog($settings, self::mock());
            $cache = $this->store->read('ndi-cache.json');
            if (isset($cache['status'])) {
                if (($cache['key'] ?? '') === $this->key($settings)) $cache['status']['events'] = [];
                else unset($cache['key']); // A previously enabled cache must not resurrect cleared events.
                $this->store->write('ndi-cache.json', $cache);
            }
            return ['status' => 'ok', 'events' => []];
        });
    }

    public function test(array $input): array
    {
        $settings = NdiSettings::validate(array_replace(NdiSettings::defaults(), $input, ['enabled' => true]));
        return $this->probe($settings, fn(): bool => false, random_int(1,100000));
    }

    private function base(array $settings): array
    {
        return ['enabled' => true, 'online' => false, 'server' => NdiSettings::server($settings), 'latency_ms' => null,
            'last_updated' => gmdate('Y-m-d\TH:i:s\Z'), 'sources_count' => 0, 'sources' => [],
            'polling_rate' => $settings['polling_rate'], 'mock' => self::mock(),
            'sources_observed' => false, 'receivers' => [], 'receivers_count' => 0,
            'receivers_supported' => false, 'receivers_observed' => false, 'receivers_warning' => null];
    }

    private function probe(array $settings, callable $cancel, int $sequence): array
    {
        $base = $this->base($settings);
        if (self::mock()) {
            $scenario = $settings['mock_scenario'];
            $sources = [];
            foreach ([['CAM-01','PTZ Plano General','192.168.10.51:5961'], ['CAM-02','PTZ Escenario','192.168.10.52:5961'],
                ['VMIX-MAIN','Program Out','192.168.10.60:5961'], ['PROPRESENTER','Lower Thirds','192.168.10.65:5962'],
                ['DECODER-HALL','HDMI In','192.168.10.70:5961']] as [$host,$stream,$url]) $sources[] = ['full_name' => "{$host} ({$stream})", 'host' => $host, 'stream' => $stream, 'url' => $url];
            $online = $scenario !== 'offline';
            $observed = $online && $scenario !== 'missing_lib';
            $receivers = $scenario === 'normal' ? [
                ['id' => 'mock-hall', 'full_name' => 'DECODER-HALL (Salida HDMI)', 'host' => 'DECODER-HALL', 'stream' => 'Salida HDMI', 'url' => '192.168.10.70'],
                ['id' => 'mock-vmix', 'full_name' => 'VMIX-MAIN (Entrada cámaras)', 'host' => 'VMIX-MAIN', 'stream' => 'Entrada cámaras', 'url' => '192.168.10.60']] : [];
            if ($scenario !== 'normal') $sources = [];
            return array_replace($base, ['online' => $online, 'latency_ms' => $online ? 1 + $sequence % 4 : null,
                'sources' => $sources, 'sources_count' => count($sources), 'sources_observed' => $observed,
                'receivers' => $receivers, 'receivers_count' => count($receivers), 'receivers_supported' => $observed,
                'receivers_observed' => $observed, 'warning' => match ($scenario) {
                    'offline' => 'Servidor sin respuesta: timeout de conexión (simulado).',
                    'missing_lib' => 'Falta libndi para listar fuentes. Consulta la instalación del runtime NDI en el README (simulado).', default => null}]);
        }
        $directory = $this->store->directory . '/ndi-run-' . bin2hex(random_bytes(12));
        if (!mkdir($directory, 0700)) return $base + ['warning' => 'No se puede preparar la consulta NDI.'];
        $process = null; $pipes = [];
        try {
            file_put_contents($directory . '/ndi-config.v1.json', json_encode(NdiSettings::sdkConfiguration($settings), JSON_THROW_ON_ERROR));
            chmod($directory . '/ndi-config.v1.json', 0600);
            $python = getenv('FLUGE_PYTHON') ?: (PHP_OS_FAMILY === 'Windows' ? 'python' : '/usr/bin/python3');
            $process = proc_open([$python, dirname(__DIR__) . '/bin/ndi-probe.py'], [['pipe','r'],['pipe','w'],['pipe','w']], $pipes, null, null, ['bypass_shell' => true]);
            if (!is_resource($process)) return $base + ['warning' => 'Python no está disponible para consultar NDI.'];
            fwrite($pipes[0], json_encode($settings + ['config_dir' => $directory], JSON_THROW_ON_ERROR)); fclose($pipes[0]); unset($pipes[0]);
            stream_set_blocking($pipes[1], false); stream_set_blocking($pipes[2], false);
            $output = ''; $deadline = microtime(true) + $settings['timeout'] + 2;
            do {
                $output .= stream_get_contents($pipes[1]);
                stream_get_contents($pipes[2]);
                if ($cancel()) { proc_terminate($process); return $base + ['warning' => 'Consulta cancelada al cambiar los ajustes.']; }
                if (microtime(true) >= $deadline || strlen($output) > 2097152) { proc_terminate($process); return $base + ['warning' => 'La consulta NDI excedió su tiempo o tamaño máximo.']; }
                $running = proc_get_status($process)['running'];
                if ($running) usleep(50000);
            } while ($running);
            $output .= stream_get_contents($pipes[1]);
            $result = json_decode($output, true);
            if (!is_array($result) || !isset($result['online'], $result['sources']) || !is_bool($result['online']) || !is_array($result['sources'])) return $base + ['warning' => 'El helper NDI no devolvió una respuesta válida. Revisa Python y libndi.'];
            return array_replace($base, array_intersect_key($result, array_flip(['online','latency_ms','sources_count','sources','warning',
                'sources_observed','receivers','receivers_count','receivers_supported','receivers_observed','receivers_warning'])));
        } finally {
            if (is_resource($process) && proc_get_status($process)['running']) {
                proc_terminate($process); usleep(50000);
                if (proc_get_status($process)['running']) proc_terminate($process, 9);
            }
            foreach ($pipes as $pipe) if (is_resource($pipe)) fclose($pipe);
            if (is_resource($process)) proc_close($process);
            foreach (glob($directory . '/*') ?: [] as $file) if (is_file($file) || is_link($file)) unlink($file);
            rmdir($directory);
        }
    }
}
