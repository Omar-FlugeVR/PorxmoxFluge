<?php
declare(strict_types=1);
namespace Homelab;

/** Locks use a separate inode so replacing a JSON file cannot bypass a lock. */
final class JsonStore
{
    public function __construct(public readonly string $directory)
    {
        if (!is_dir($directory) || !is_readable($directory)) {
            throw new HttpError(503, 'El almacenamiento privado no está inicializado.');
        }
    }

    public function path(string $name): string
    {
        if (!preg_match('/^[a-z][a-z0-9-]*\.(json|conf)$/D', $name)) {
            throw new \InvalidArgumentException('Invalid state filename');
        }
        return $this->directory . '/' . $name;
    }

    public function read(string $name, array $fallback = []): array
    {
        $path = $this->path($name);
        if (!is_file($path)) {
            return $fallback;
        }
        $raw = file_get_contents($path);
        try {
            $data = json_decode($raw === false ? '' : $raw, true, 64, JSON_THROW_ON_ERROR);
        } catch (\JsonException $error) {
            throw new HttpError(503, 'Un archivo de estado no es válido; restaura la copia de seguridad.');
        }
        if (!is_array($data)) {
            throw new HttpError(503, 'Un archivo de estado tiene un formato incorrecto.');
        }
        return $data;
    }

    public function locked(string $key, callable $operation): mixed
    {
        if (!preg_match('/^[a-z][a-z0-9-]*$/D', $key)) {
            throw new \InvalidArgumentException('Invalid lock name');
        }
        $lock = fopen($this->directory . '/' . $key . '.lock', 'c');
        if ($lock === false) {
            throw new HttpError(503, 'No se puede bloquear el almacenamiento.');
        }
        try {
            if (!flock($lock, LOCK_EX)) {
                throw new HttpError(503, 'No se puede bloquear el almacenamiento.');
            }
            return $operation();
        } finally {
            flock($lock, LOCK_UN);
            fclose($lock);
        }
    }

    public function write(string $name, array $data): void
    {
        $json = json_encode($data, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR) . "\n";
        $path = $this->path($name);
        $temporary = tempnam($this->directory, '.state-');
        if ($temporary === false) {
            throw new HttpError(503, 'No se puede crear el archivo temporal.');
        }
        try {
            chmod($temporary, 0600);
            $handle = fopen($temporary, 'wb');
            if ($handle === false) {
                throw new HttpError(503, 'No se puede abrir el archivo temporal.');
            }
            try {
                $length = strlen($json);
                $written = 0;
                while ($written < $length) {
                    $count = fwrite($handle, substr($json, $written));
                    if ($count === false || $count === 0) {
                        throw new HttpError(503, 'No se puede guardar el estado.');
                    }
                    $written += $count;
                }
                if (!fflush($handle) || (function_exists('fsync') && !fsync($handle))) {
                    throw new HttpError(503, 'No se puede confirmar el estado en disco.');
                }
            } finally {
                fclose($handle);
            }
            if (!rename($temporary, $path)) {
                throw new HttpError(503, 'No se puede publicar el estado.');
            }
        } finally {
            if (is_file($temporary)) {
                unlink($temporary);
            }
        }
    }
}
