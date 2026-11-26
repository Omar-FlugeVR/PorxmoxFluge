<?php
declare(strict_types=1);
namespace Homelab;

final class UpdateJobs
{
    private JsonStore $reports;

    public function __construct(private JsonStore $store)
    {
        $this->reports = getenv('FLUGE_DEMO') === '1' ? $store : new JsonStore('/etc/fluge-control');
    }

    public function report(): array
    {
        $request = $this->store->read('update-request.json');
        $job = $this->reports->read('update-job.json');
        $report = $this->reports->read('updates.json', ['checkedAt' => null, 'services' => []]);
        if ($request && ($job['id'] ?? null) !== $request['id']) {
            $job = $request + ['state' => time() - $request['created'] > 900 ? 'expired' : 'queued'];
        }
        if ($job) $report['job'] = $job;
        $automatic = $this->reports->read('update-check.json');
        if (is_int($automatic['expiresAt'] ?? null) && $automatic['expiresAt'] > time()) {
            $report['automaticCheck'] = true;
        }
        return $report;
    }

    public function enqueueCheck(): array
    {
        return $this->store->locked('update-request', function (): array {
            $report = $this->report();
            $job = $report['job'] ?? [];
            if (in_array($job['state'] ?? '', ['queued', 'running'], true)) {
                if ($job['role'] === 'check') return $job;
                throw new HttpError(409, 'Ya hay una actualización en curso.');
            }
            if ($report['automaticCheck'] ?? false) {
                throw new HttpError(409, 'Ya hay una búsqueda automática en curso.');
            }
            $request = ['id' => bin2hex(random_bytes(16)), 'role' => 'check', 'created' => time()];
            $this->store->write('update-request.json', $request);
            if (getenv('FLUGE_DEMO') === '1') {
                $report['checkedAt'] = gmdate(DATE_ATOM);
                unset($report['job']);
                $this->reports->write('updates.json', $report);
                $job = $request + ['state' => 'success'];
                $this->reports->write('update-job.json', $job);
                return $job;
            }
            return $request + ['state' => 'queued'];
        });
    }

    public function enqueue(array $input): array
    {
        $role = $input['role'] ?? '';
        $version = $input['version'] ?? '';
        if (!in_array($role, ['panel', 'caddy', 'adguard', 'companion', 'ndi'], true) || !is_string($version)) {
            throw new HttpError(422, 'Selecciona un servicio y una versión válidos.');
        }
        return $this->store->locked('update-request', function () use ($input, $role, $version): array {
            $report = $this->report();
            if (($report['automaticCheck'] ?? false) || in_array($report['job']['state'] ?? '', ['queued', 'running'], true)) {
                throw new HttpError(409, 'Ya hay una comprobación o actualización en curso.');
            }
            $entry = $report['services'][$role] ?? [];
            $checkedAt = strtotime($report['checkedAt'] ?? '');
            if (!$checkedAt || time() - $checkedAt > 7200 || ($entry['status'] ?? '') !== 'update'
                || $version !== ($entry['latest'] ?? null) || !($entry['canUpdate'] ?? false)) {
                throw new HttpError(409, 'La versión seleccionada ya no está disponible en el informe.');
            }
            $request = ['id' => bin2hex(random_bytes(16)), 'role' => $role, 'version' => $version, 'created' => time()];
            if ($role === 'panel') {
                $steps = array_column($entry['upgradePath'] ?? [], 'commit');
                if (($input['upgradePath'] ?? []) !== $steps) {
                    throw new HttpError(409, 'La ruta de actualización cambió. Vuelve a comprobar las versiones.');
                }
                if ($steps) $request['planHash'] = hash('sha256', implode(',', $steps));
            }
            if ($entry['license'] ?? null) {
                if (($input['accepted'] ?? false) !== true || ($input['licenseHash'] ?? '') !== $entry['license']['hash']) {
                    throw new HttpError(422, 'Acepta la licencia de NDI para continuar.');
                }
                $request['licenseHash'] = $entry['license']['hash'];
            }
            $this->store->write('update-request.json', $request);
            if (getenv('FLUGE_DEMO') === '1') {
                $report['services'][$role]['installed'] = $version;
                $report['services'][$role]['status'] = 'current';
                if ($role === 'panel') {
                    $report['services'][$role]['installedRelease'] = $entry['latestRelease'];
                    unset($report['services'][$role]['upgradePath']);
                }
                unset($report['job']);
                $this->reports->write('updates.json', $report);
                $this->reports->write('update-job.json', $request + ['state' => 'success']);
                return $request + ['state' => 'success'];
            }
            return $request + ['state' => 'queued'];
        });
    }
}
