<?php
declare(strict_types=1);
namespace Homelab;

final class Auth
{
    private const LOGIN_BLOCK_SECONDS = [60, 60, 60, 300, 300, 300, 600];
    private const LOGIN_STATE = 'login-attempts.json';

    private UsersRepository $users;

    public function __construct(private JsonStore $store, private ConfigRepository $config)
    {
        $this->users = new UsersRepository($store, $config);
    }

    public function authenticated(): bool
    {
        if (!isset($_SESSION['identity'], $_SESSION['credentialFingerprint'], $_SESSION['lastActivity'])) {
            return false;
        }
        // Existing linked-admin sessions survive the introduction of local accounts.
        $identity = $this->users->identity($_SESSION['userId'] ?? $this->users->primary()['id']);
        if ($identity === null || !$identity['enabled'] || !hash_equals($identity['fingerprint'], $_SESSION['credentialFingerprint']) || $_SESSION['lastActivity'] < time() - 8 * 3600) {
            unset($_SESSION['identity'], $_SESSION['userId'], $_SESSION['role'], $_SESSION['authMode'], $_SESSION['credentialFingerprint'], $_SESSION['lastActivity']);
            return false;
        }
        // Role/status always come from current server state, never the request/session.
        $_SESSION['identity'] = $identity['username'];
        $_SESSION['userId'] = $identity['id'];
        $_SESSION['role'] = $identity['role'];
        $_SESSION['authMode'] = $identity['authMode'];
        $_SESSION['lastActivity'] = time();
        return true;
    }

    public function requireAuthenticated(): void
    {
        if (!$this->authenticated()) {
            throw new HttpError(401, 'Inicia sesión para continuar.');
        }
    }

    public function requireAdmin(): void
    {
        $this->requireAuthenticated();
        if ($_SESSION['role'] !== 'admin') {
            throw new HttpError(403, 'Esta acción requiere permisos de administrador.');
        }
    }

    public function userId(): string
    {
        $this->requireAuthenticated();
        return $_SESSION['userId'];
    }

    public function session(): array
    {
        $authenticated = $this->authenticated();
        $_SESSION['csrfToken'] ??= bin2hex(random_bytes(32));
        return ['authenticated' => $authenticated, 'userId' => $authenticated ? $_SESSION['userId'] : null, 'username' => $authenticated ? $_SESSION['identity'] : null, 'role' => $authenticated ? $_SESSION['role'] : null, 'isPrimary' => $authenticated && $_SESSION['userId'] === $this->users->primary()['id'], 'authMode' => $authenticated ? $_SESSION['authMode'] : $this->users->primary()['authMode'], 'csrfToken' => $_SESSION['csrfToken']];
    }

    public function csrf(mixed $token): void
    {
        if (!is_string($token) || !isset($_SESSION['csrfToken']) || !hash_equals($_SESSION['csrfToken'], $token)) {
            throw new HttpError(403, 'La sesión de seguridad ha cambiado. Recarga la página.');
        }
    }

    private static function blockedUntil(mixed $entry): int
    {
        if (!is_array($entry)) {
            return 0;
        }
        // Las instalaciones anteriores guardaban inicio y contador de una ventana de 15 minutos.
        return (int) ($entry['blockedUntil'] ?? (($entry['count'] ?? 0) >= 5 ? ($entry['start'] ?? 0) + 900 : 0));
    }

    public function loginRetryAfter(string $remoteAddress): int
    {
        $attempts = $this->store->read(self::LOGIN_STATE);
        return max(0, self::blockedUntil($attempts[hash('sha256', $remoteAddress)] ?? null) - time());
    }

    private static function requireLoginReady(int $retryAfter): void
    {
        if ($retryAfter > 0) {
            throw new HttpError(429, 'Acceso bloqueado temporalmente por demasiados intentos.', ['retry_after' => $retryAfter]);
        }
    }

    private function recordLoginResult(string $remoteAddress, bool $successful): void
    {
        $this->store->locked('login-attempts', function () use ($remoteAddress, $successful): void {
            $attempts = $this->store->read(self::LOGIN_STATE);
            $now = time();
            $key = hash('sha256', $remoteAddress);
            self::requireLoginReady(self::blockedUntil($attempts[$key] ?? null) - $now);
            if ($successful) {
                if (isset($attempts[$key])) {
                    unset($attempts[$key]);
                    $this->store->write(self::LOGIN_STATE, $attempts);
                }
                return;
            }

            $attempts = array_filter($attempts, static function ($entry) use ($now): bool {
                return is_array($entry) && (isset($entry['updatedAt'])
                    ? $entry['updatedAt'] > $now - 86400
                    : ($entry['start'] ?? 0) > $now - 900);
            });
            if (!isset($attempts[$key]) && count($attempts) >= 5000) {
                $unblocked = array_key_first(array_filter($attempts, static fn(array $entry): bool => self::blockedUntil($entry) <= $now));
                if ($unblocked === null) {
                    self::requireLoginReady(min(array_map(self::blockedUntil(...), $attempts)) - $now);
                }
                unset($attempts[$unblocked]);
            }

            $entry = $attempts[$key] ?? [];
            $count = ($entry['count'] ?? 0) + 1;
            $blocks = (int) ($entry['blocks'] ?? 0);
            $delay = 0;
            if ($count >= 5) {
                $lastStage = count(self::LOGIN_BLOCK_SECONDS) - 1;
                $delay = self::LOGIN_BLOCK_SECONDS[min($blocks, $lastStage)];
                $blocks = min($blocks + 1, $lastStage);
                $count = 0;
            }
            $attempts[$key] = ['count' => $count, 'blocks' => $blocks,
                'blockedUntil' => $delay > 0 ? $now + $delay : 0, 'updatedAt' => $now];
            $this->store->write(self::LOGIN_STATE, $attempts);
            self::requireLoginReady($delay);
        });
    }

    public function login(array $input, string $remoteAddress): array
    {
        self::requireLoginReady($this->loginRetryAfter($remoteAddress));
        try {
            $username = Validator::text($input['username'] ?? '', 'username', 128, true);
        } catch (HttpError $error) {
            $this->recordLoginResult($remoteAddress, false);
            throw $error;
        }
        $password = $input['password'] ?? '';
        if (!is_string($password) || strlen($password) > 1024 || str_contains($password, "\0")) {
            $this->recordLoginResult($remoteAddress, false);
            throw new HttpError(401, 'Usuario o contraseña incorrectos.');
        }
        $identity = $this->users->byUsername($username);
        $mode = $identity['authMode'] ?? null;
        $passwordMatches = $mode === 'adguard'
            ? hash_equals(hash('sha256', $identity['password']), hash('sha256', $password))
            : PasswordPolicy::verify($password, $identity['passwordHash'] ?? '$2y$10$92IXUNpkjO0rOQ5byMi.Ye4oKoEa3Ro9llC/.og/at2uheWG/igi.');
        $successful = $identity !== null && $identity['enabled'] && $passwordMatches;
        $this->recordLoginResult($remoteAddress, $successful);
        if (!$successful) {
            throw new HttpError(401, 'Usuario o contraseña incorrectos.');
        }
        session_regenerate_id(true);
        $_SESSION = ['identity' => $identity['username'], 'userId' => $identity['id'], 'role' => $identity['role'], 'authMode' => $mode, 'credentialFingerprint' => $identity['fingerprint'], 'lastActivity' => time(), 'csrfToken' => bin2hex(random_bytes(32))];
        return $this->session();
    }

    public function logout(): array
    {
        $_SESSION = ['csrfToken' => bin2hex(random_bytes(32))];
        session_regenerate_id(true);
        return $this->session();
    }

    public function changePassword(array $input): array
    {
        $this->requireAuthenticated();
        $actorId = $_SESSION['userId'];
        $identity = $this->users->changePassword($input, $actorId, $_SESSION['credentialFingerprint']);
        if (($input['id'] ?? null) === $actorId) {
            // Preserve this session only; older sessions must use the new password.
            $_SESSION['userId'] = $identity['id'];
            $_SESSION['authMode'] = $identity['authMode'];
            $_SESSION['credentialFingerprint'] = $identity['fingerprint'];
        }
        $_SESSION['csrfToken'] = bin2hex(random_bytes(32));
        session_regenerate_id(true);
        return ['status' => 'ok'] + $this->session();
    }
}
