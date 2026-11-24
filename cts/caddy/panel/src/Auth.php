<?php
declare(strict_types=1);
namespace Homelab;

final class Auth
{
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

    private function consumeLoginAttempt(string $remoteAddress): void
    {
        $this->store->locked('login-attempts', function () use ($remoteAddress): void {
            $attempts = $this->store->read('login-attempts.json');
            $now = time();
            $attempts = array_filter($attempts, static fn($entry): bool => is_array($entry) && ($entry['start'] ?? 0) > $now - 900);
            $key = hash('sha256', $remoteAddress);
            if (isset($attempts[$key]) && ($attempts[$key]['count'] ?? 0) >= 5) {
                throw new HttpError(429, 'Demasiados intentos. Espera 15 minutos antes de intentarlo de nuevo.');
            }
            if (!isset($attempts[$key]) && count($attempts) >= 5000) {
                throw new HttpError(429, 'Demasiados intentos de acceso. Inténtalo más tarde.');
            }
            $attempts[$key] ??= ['start' => $now, 'count' => 0];
            ++$attempts[$key]['count'];
            $this->store->write('login-attempts.json', $attempts);
        });
    }

    public function login(array $input, string $remoteAddress): array
    {
        $this->consumeLoginAttempt($remoteAddress);
        $username = Validator::text($input['username'] ?? '', 'username', 128, true);
        $password = $input['password'] ?? '';
        if (!is_string($password) || strlen($password) > 1024 || str_contains($password, "\0")) {
            throw new HttpError(401, 'Usuario o contraseña incorrectos.');
        }
        $identity = $this->users->byUsername($username);
        $mode = $identity['authMode'] ?? null;
        $passwordMatches = $mode === 'adguard'
            ? hash_equals(hash('sha256', $identity['password']), hash('sha256', $password))
            : PasswordPolicy::verify($password, $identity['passwordHash'] ?? '$2y$10$92IXUNpkjO0rOQ5byMi.Ye4oKoEa3Ro9llC/.og/at2uheWG/igi.');
        if ($identity === null || !$identity['enabled'] || !$passwordMatches) {
            throw new HttpError(401, 'Usuario o contraseña incorrectos.');
        }
        $this->store->locked('login-attempts', function () use ($remoteAddress): void {
            $attempts = $this->store->read('login-attempts.json');
            unset($attempts[hash('sha256', $remoteAddress)]);
            $this->store->write('login-attempts.json', $attempts);
        });
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
