<?php
declare(strict_types=1);
use Homelab\CaddyRenderer;
require dirname(__DIR__) . '/src/autoload.php';
if (PHP_SAPI !== 'cli') exit(1);
$site = json_decode(file_get_contents('/etc/fluge-control/site.json'), true, 64, JSON_THROW_ON_ERROR);
$candidate = json_decode(stream_get_contents(STDIN), true, 64, JSON_THROW_ON_ERROR);
echo CaddyRenderer::renderCandidate($candidate, [$site['dashboardDomain']]);
