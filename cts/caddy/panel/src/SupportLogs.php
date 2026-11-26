<?php
declare(strict_types=1);
namespace Homelab;

final class SupportLogs
{
    private const LIFETIME = 600;

    public function __construct(private JsonStore $store) {}

    private function demo(): bool
    {
        return getenv('FLUGE_DEMO') === '1';
    }

    private function archivePath(string $id): string
    {
        $directory = $this->demo() ? $this->store->directory : '/etc/fluge-control';
        return $directory . '/support-logs-' . $id . '.zip';
    }

    private function currentArchive(string $id): bool
    {
        $path = $this->archivePath($id);
        return is_file($path) && filemtime($path) > time() - self::LIFETIME;
    }

    public function enqueue(): array
    {
        return $this->store->locked('support-logs', function (): array {
            $request = $this->store->read('support-request.json');
            if (isset($request['id']) && $this->status($request['id'])['state'] === 'queued') {
                return ['id' => $request['id'], 'state' => 'queued'];
            }
            $id = bin2hex(random_bytes(16));
            if ($this->demo()) $this->buildDemo($id);
            $this->store->write('support-request.json', ['id' => $id, 'created' => time()]);
            return $this->status($id);
        });
    }

    public function status(mixed $id): array
    {
        if (!is_string($id) || !preg_match('/\A[a-f0-9]{32}\z/', $id)) {
            throw new HttpError(400, 'Identificador de descarga no válido.');
        }
        if ($this->currentArchive($id)) return ['id' => $id, 'state' => 'ready'];
        $request = $this->store->read('support-request.json');
        $queued = ($request['id'] ?? null) === $id && $request['created'] > time() - self::LIFETIME;
        return ['id' => $id, 'state' => $queued ? 'queued' : 'expired'];
    }

    public function download(mixed $id): void
    {
        if ($this->status($id)['state'] !== 'ready') throw new HttpError(404, 'El ZIP no está disponible.');
        $file = @fopen($this->archivePath($id), 'rb');
        if ($file === false) throw new HttpError(404, 'El ZIP no está disponible.');
        session_write_close();
        try {
            header('Content-Type: application/zip');
            header('Content-Disposition: attachment; filename="fluge-logs-' . gmdate('Y-m-d_His') . '.zip"');
            header('Content-Length: ' . fstat($file)['size']);
            fpassthru($file);
        } finally {
            fclose($file);
        }
    }

    private function buildDemo(string $id): void
    {
        $python = getenv('FLUGE_PYTHON') ?: (PHP_OS_FAMILY === 'Windows' ? 'python' : '/usr/bin/python3');
        $process = proc_open([$python, dirname(__DIR__) . '/bin/support-logs.py', '--demo'],
            [['pipe', 'r'], ['pipe', 'w'], ['file', PHP_OS_FAMILY === 'Windows' ? 'NUL' : '/dev/null', 'a']],
            $pipes, null, null, ['bypass_shell' => true]);
        if (!is_resource($process)) throw new HttpError(503, 'No se pudo generar el ZIP de muestra.');
        fclose($pipes[0]);
        $data = stream_get_contents($pipes[1]);
        fclose($pipes[1]);
        if (proc_close($process) !== 0 || !$data) throw new HttpError(503, 'No se pudo generar el ZIP de muestra.');
        $path = $this->archivePath($id);
        if (file_put_contents($path, $data) !== strlen($data)) throw new HttpError(503, 'No se pudo guardar el ZIP de muestra.');
        foreach (glob($this->store->directory . '/support-logs-*.zip') ?: [] as $previous) {
            if ($previous !== $path) @unlink($previous);
        }
    }
}
