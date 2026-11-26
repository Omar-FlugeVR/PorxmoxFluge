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
        $report = $this->reports->read('updates.json', ['checkedAt' => null, 'services' => []]);
        $request = $this->store->read('update-request.json');
        $job = $this->reports->read('update-job.json');
        if ($request && ($job['id'] ?? null) !== $request['id']) {
            $job = $request + ['state' => time() - $request['created'] > 900 ? 'expired' : 'queued'];
        }
        if ($job) $report['job'] = $job;
        return $report;
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
            if (in_array($report['job']['state'] ?? '', ['queued', 'running'], true)) {
                throw new HttpError(409, 'Ya hay una actualización en curso.');
            }
            $entry = $report['services'][$role] ?? [];
            $checkedAt = strtotime($report['checkedAt'] ?? '');
            if (!$checkedAt || time() - $checkedAt > 7200 || ($entry['status'] ?? '') !== 'update'
                || $version !== ($entry['latest'] ?? null) || !($entry['canUpdate'] ?? false)
                || ($role === 'panel' && ($entry['latestRelease']['update_required'] ?? false) !== true)) {
                throw new HttpError(409, 'La versión seleccionada ya no está disponible en el informe.');
            }
            $request = ['id' => bin2hex(random_bytes(16)), 'role' => $role, 'version' => $version, 'created' => time()];
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
                if ($role === 'panel') $report['services'][$role]['installedRelease'] = $entry['latestRelease'];
                unset($report['job']);
                $this->reports->write('updates.json', $report);
                $this->reports->write('update-job.json', $request + ['state' => 'success']);
                return $request + ['state' => 'success'];
            }
            return $request + ['state' => 'queued'];
        });
    }
}
