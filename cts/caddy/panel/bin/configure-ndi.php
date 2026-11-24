<?php
declare(strict_types=1);

use Homelab\{ConfigRepository, JsonStore, NdiSettings};

require dirname(__DIR__) . '/src/autoload.php';
if (PHP_SAPI !== 'cli' || $argc !== 4) {
    fwrite(STDERR, "Uso: php configure-ndi.php STATE IP PUERTO\n");
    exit(1);
}

[, $directory, $ip, $port] = $argv;
$settings = $ip === '' ? NdiSettings::defaults() : ['enabled' => true, 'ip' => $ip, 'port' => (int) $port];
(new ConfigRepository(new JsonStore($directory)))->applyNdiDefault($settings);
