<?php
declare(strict_types=1);
namespace Homelab;

final class DnsSynchronizer
{
    public function __construct(private JsonStore $store) {}

    public function sync(array $desired, AdGuardClient $client): array
    {
        $existing = [];
        foreach ($client->list() as $entry) {
            if (is_array($entry) && isset($entry['domain'], $entry['answer']) && is_string($entry['domain']) && is_string($entry['answer'])) {
                $existing[strtolower($entry['domain']) . "\0" . $entry['answer']] = ['domain' => strtolower($entry['domain']), 'answer' => $entry['answer'], 'enabled' => $entry['enabled'] ?? true];
            }
        }
        $owned = [];
        foreach ($this->store->read('managed-dns.json') as $entry) {
            if (is_array($entry) && isset($entry['domain'], $entry['answer'])) {
                $owned[$entry['domain'] . "\0" . $entry['answer']] = $entry;
            }
        }
        $errors = [];
        $ready = [];
        $added = 0;
        $deleted = 0;
        foreach ($desired as $domain => $answer) {
            $key = $domain . "\0" . $answer;
            foreach ($existing as $existingKey => $entry) {
                if ($entry['domain'] === $domain && $entry['answer'] !== $answer && !isset($owned[$existingKey])) {
                    $errors[] = 'La regla ' . $domain . ' pertenece a otra configuración; no se ha modificado.';
                    continue 2;
                }
            }
            if (isset($existing[$key])) {
                if (!$existing[$key]['enabled']) {
                    $errors[] = 'La regla ' . $domain . ' está desactivada en AdGuard. Actívala antes de aplicar.';
                    continue;
                }
                $ready[$domain] = true;
                // Existing matching entries remain external unless we created them previously.
                continue;
            }
            try {
                $client->add($domain, $answer);
                $owned[$key] = $existing[$key] = compact('domain', 'answer');
                $this->store->write('managed-dns.json', array_values($owned));
                $ready[$domain] = true;
                ++$added;
            } catch (HttpError $error) {
                $errors[] = $domain . ': ' . $error->getMessage();
            }
        }
        foreach ($owned as $key => $entry) {
            $domain = $entry['domain'];
            if (($desired[$domain] ?? null) === $entry['answer']) {
                continue;
            }
            // Keep the previous answer when its replacement could not be installed.
            if (isset($desired[$domain]) && !isset($ready[$domain])) {
                continue;
            }
            try {
                if (isset($existing[$key])) {
                    $client->delete($domain, $entry['answer']);
                    ++$deleted;
                }
                unset($owned[$key]);
                $this->store->write('managed-dns.json', array_values($owned));
            } catch (HttpError $error) {
                $errors[] = $domain . ': ' . $error->getMessage();
            }
        }
        return ['ok' => $errors === [], 'synced_count' => count($ready), 'added' => $added, 'deleted' => $deleted, 'errors' => $errors];
    }
}
