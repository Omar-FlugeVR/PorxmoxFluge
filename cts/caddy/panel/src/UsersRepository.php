<?php
declare(strict_types=1);
namespace Homelab;

/** Local users and the protected primary administrator share one namespace. */
final class UsersRepository
{
    public function __construct(private JsonStore $store, private ConfigRepository $config) {}

    public function get(): array
    {
        $data = $this->store->read('users.json', ['revision' => 0, 'users' => []]);
        if (!isset($data['revision'], $data['users']) || !is_int($data['revision']) || $data['revision'] < 0 || !is_array($data['users']) || !array_is_list($data['users'])) {
            throw new HttpError(503, 'El registro de usuarios no tiene un formato válido.');
        }
        foreach ($data['users'] as $user) {
            if (!is_array($user) || !isset($user['id'], $user['username'], $user['role'], $user['enabled'], $user['passwordHash'], $user['authRevision']) || !is_string($user['id']) || !is_string($user['username']) || !in_array($user['role'], ['admin', 'user'], true) || !is_bool($user['enabled']) || !is_string($user['passwordHash']) || !is_int($user['authRevision'])) {
                throw new HttpError(503, 'El registro de usuarios contiene una cuenta no válida.');
            }
        }
        return $data;
    }

    private static function username(mixed $username): string
    {
        $username = Validator::text($username, 'username', 64, true);
        if (!preg_match('/^[A-Za-z0-9][A-Za-z0-9_.@-]{2,63}$/D', $username)) {
            throw new HttpError(422, 'El usuario debe tener de 3 a 64 letras, números o caracteres . _ @ -.');
        }
        return $username;
    }

    private static function role(mixed $role): string
    {
        if (!in_array($role, ['admin', 'user'], true)) {
            throw new HttpError(422, 'El rol debe ser admin o user.');
        }
        return $role;
    }

    public function primary(): array
    {
        $auth = $this->store->read('auth.json', ['mode' => 'adguard']);
        if (($auth['mode'] ?? 'independent') === 'adguard') {
            $config = $this->config->get();
            return ['id' => 'adguard-admin', 'username' => $config['adguardUser'], 'role' => 'admin', 'enabled' => $config['adguardUser'] !== '' && $config['adguardPass'] !== '', 'authMode' => 'adguard', 'source' => 'adguard', 'password' => $config['adguardPass'], 'fingerprint' => hash('sha256', $config['adguardUser'] . "\0" . $config['adguardPass'])];
        }
        return ['id' => 'independent-admin', 'username' => $auth['username'] ?? '', 'role' => 'admin', 'enabled' => !empty($auth['username']) && !empty($auth['passwordHash']), 'authMode' => 'independent', 'source' => 'independent', 'passwordHash' => $auth['passwordHash'] ?? '', 'fingerprint' => hash('sha256', ($auth['username'] ?? '') . "\0" . ($auth['passwordHash'] ?? '') . "\0" . ($auth['authRevision'] ?? 0))];
    }

    private static function localIdentity(array $user): array
    {
        return array_replace($user, ['authMode' => 'local', 'source' => 'local', 'fingerprint' => hash('sha256', json_encode([$user['id'], $user['username'], $user['role'], $user['enabled'], $user['passwordHash'], $user['authRevision']], JSON_THROW_ON_ERROR))]);
    }

    public function identity(string $id): ?array
    {
        $primary = $this->primary();
        if ($primary['id'] === $id) {
            return $primary;
        }
        foreach ($this->get()['users'] as $user) {
            if ($user['id'] === $id) {
                return self::localIdentity($user);
            }
        }
        return null;
    }

    public function byUsername(string $username): ?array
    {
        $primary = $this->primary();
        if (strcasecmp($username, $primary['username']) === 0) {
            return $primary;
        }
        foreach ($this->get()['users'] as $user) {
            if (strcasecmp($username, $user['username']) === 0) {
                return self::localIdentity($user);
            }
        }
        return null;
    }

    public static function publicUser(array $user): array
    {
        return ['id' => $user['id'], 'username' => $user['username'], 'role' => $user['role'], 'enabled' => $user['enabled'], 'isPrimary' => in_array($user['id'], ['independent-admin', 'adguard-admin'], true), 'source' => $user['source'] ?? 'local', 'createdAt' => $user['createdAt'] ?? null, 'updatedAt' => $user['updatedAt'] ?? null];
    }

    public function publicList(): array
    {
        $data = $this->get();
        return ['revision' => $data['revision'], 'users' => array_map(self::publicUser(...), [$this->primary(), ...$data['users']])];
    }

    /** Caller holds accounts.lock, shared with configuration/user mutations. */
    public function assertAdGuardUsernameAvailable(string $username): void
    {
        foreach ($this->get()['users'] as $user) {
            if (strcasecmp($username, $user['username']) === 0) {
                throw new HttpError(422, 'Ese nombre ya pertenece a un usuario local.');
            }
        }
        $primary = $this->primary();
        if ($primary['authMode'] === 'independent' && strcasecmp($username, $primary['username']) === 0) {
            throw new HttpError(422, 'Ese nombre pertenece al administrador independiente.');
        }
    }

    private function assertUsernameAvailable(string $username, array $users, ?string $except = null): void
    {
        foreach (array_unique([$this->primary()['username'], $this->config->get()['adguardUser']]) as $reserved) {
            if ($reserved !== '' && strcasecmp($username, $reserved) === 0) {
                throw new HttpError(422, 'Ese nombre está reservado para el administrador vinculado.');
            }
        }
        foreach ($users as $user) {
            if ($user['id'] !== $except && strcasecmp($username, $user['username']) === 0) {
                throw new HttpError(422, 'Ya existe un usuario con ese nombre.');
            }
        }
    }

    private function requireAdmin(string $actorId): void
    {
        $actor = $this->identity($actorId);
        if ($actor === null || !$actor['enabled'] || $actor['role'] !== 'admin') {
            throw new HttpError(403, 'Esta acción requiere una cuenta administradora activa.');
        }
    }

    private function assertRevision(array $data, mixed $revision): void
    {
        if (Validator::revision($revision) !== $data['revision']) {
            throw new HttpError(409, 'La lista de usuarios ha cambiado. Recarga antes de guardar.', ['revision' => $data['revision']]);
        }
    }

    private function assertAdminRemains(array $users): void
    {
        if ($this->primary()['enabled']) {
            return;
        }
        foreach ($users as $user) {
            if ($user['enabled'] && $user['role'] === 'admin') {
                return;
            }
        }
        throw new HttpError(409, 'Debe quedar al menos un administrador activo.');
    }

    public function create(array $input, string $actorId): array
    {
        return $this->store->locked('accounts', function () use ($input, $actorId): array {
            $this->requireAdmin($actorId);
            $data = $this->get();
            $this->assertRevision($data, $input['revision'] ?? null);
            if (count($data['users']) >= 100) {
                throw new HttpError(422, 'Se ha alcanzado el máximo de 100 usuarios locales.');
            }
            $username = self::username($input['username'] ?? null);
            $this->assertUsernameAvailable($username, $data['users']);
            $role = self::role($input['role'] ?? null);
            $password = PasswordPolicy::confirmed($input['password'] ?? null, $input['confirmPassword'] ?? null);
            $enabled = array_key_exists('enabled', $input) ? $input['enabled'] : true;
            if (!is_bool($enabled)) {
                throw new HttpError(422, 'enabled debe ser booleano.');
            }
            $now = gmdate('c');
            $user = ['id' => 'user-' . bin2hex(random_bytes(16)), 'username' => $username, 'role' => $role, 'enabled' => $enabled, 'passwordHash' => PasswordPolicy::hash($password), 'authRevision' => 1, 'createdAt' => $now, 'updatedAt' => $now];
            $data['users'][] = $user;
            ++$data['revision'];
            $this->store->write('users.json', $data);
            return ['status' => 'ok', 'revision' => $data['revision'], 'user' => self::publicUser($user)];
        });
    }

    public function update(array $input, string $actorId): array
    {
        return $this->store->locked('accounts', function () use ($input, $actorId): array {
            $this->requireAdmin($actorId);
            $data = $this->get();
            $this->assertRevision($data, $input['revision'] ?? null);
            $id = Validator::text($input['id'] ?? null, 'id', 64, true);
            if (in_array($id, ['adguard-admin', 'independent-admin'], true)) {
                throw new HttpError(409, 'La cuenta principal no se puede desactivar ni modificar desde esta acción.');
            }
            if (array_key_exists('password', $input) || array_key_exists('newPassword', $input)) throw new HttpError(403, 'Las contraseñas solo las puede cambiar la cuenta principal desde Cambiar contraseña.');
            foreach ($data['users'] as $index => $current) {
                if ($current['id'] !== $id) {
                    continue;
                }
                $username = self::username($input['username'] ?? null);
                $this->assertUsernameAvailable($username, $data['users'], $id);
                $role = self::role($input['role'] ?? null);
                $enabled = $input['enabled'] ?? null;
                if (!is_bool($enabled)) {
                    throw new HttpError(422, 'enabled debe ser booleano.');
                }
                if ($id === $actorId && (!$enabled || $role !== 'admin')) {
                    throw new HttpError(409, 'No puedes desactivar ni degradar tu propia cuenta administradora.');
                }
                $user = array_replace($current, compact('username', 'role', 'enabled'));
                if ($user !== $current) {
                    ++$user['authRevision'];
                    $user['updatedAt'] = gmdate('c');
                }
                $data['users'][$index] = $user;
                $this->assertAdminRemains($data['users']);
                ++$data['revision'];
                $this->store->write('users.json', $data);
                return ['status' => 'ok', 'revision' => $data['revision'], 'user' => self::publicUser($user)];
            }
            throw new HttpError(404, 'Usuario no encontrado.');
        });
    }

    public function delete(array $input, string $actorId): array
    {
        return $this->store->locked('accounts', function () use ($input, $actorId): array {
            $this->requireAdmin($actorId);
            $data = $this->get();
            $this->assertRevision($data, $input['revision'] ?? null);
            $id = Validator::text($input['id'] ?? null, 'id', 64, true);
            if ($id === $actorId || in_array($id, ['adguard-admin', 'independent-admin'], true)) {
                throw new HttpError(409, 'No puedes eliminar tu propia cuenta ni la cuenta principal.');
            }
            $remaining = array_values(array_filter($data['users'], static fn(array $user): bool => $user['id'] !== $id));
            if (count($remaining) === count($data['users'])) {
                throw new HttpError(404, 'Usuario no encontrado.');
            }
            $this->assertAdminRemains($remaining);
            $data['users'] = $remaining;
            ++$data['revision'];
            $this->store->write('users.json', $data);
            return ['status' => 'ok', 'revision' => $data['revision']];
        });
    }

    public function changePassword(array $input, string $actorId, string $actorFingerprint): array
    {
        return $this->store->locked('accounts', function () use ($input, $actorId, $actorFingerprint): array {
            $primary = $this->primary();
            if (!$primary['enabled'] || $actorId !== $primary['id']) throw new HttpError(403, 'Solo la cuenta principal puede cambiar contraseñas.');
            if (!hash_equals($primary['fingerprint'], $actorFingerprint)) throw new HttpError(401, 'La sesión ha cambiado. Inicia sesión de nuevo.');
            $data = $this->get();
            $this->assertRevision($data, $input['revision'] ?? null);
            $id = Validator::text($input['id'] ?? null, 'id', 64, true);
            $newPassword = PasswordPolicy::confirmed($input['newPassword'] ?? null, $input['confirmPassword'] ?? null);
            if ($id === $primary['id']) {
                $auth = $this->store->read('auth.json');
                // Older linked accounts become independent; AdGuard credentials stay intact.
                $this->store->write('auth.json', ['mode' => 'independent', 'username' => $primary['username'],
                    'passwordHash' => PasswordPolicy::hash($newPassword), 'authRevision' => ($auth['authRevision'] ?? 0) + 1]);
                return $this->primary();
            }
            foreach ($data['users'] as $index => $user) {
                if ($user['id'] !== $id) {
                    continue;
                }
                $user['passwordHash'] = PasswordPolicy::hash($newPassword);
                ++$user['authRevision'];
                $user['updatedAt'] = gmdate('c');
                $data['users'][$index] = $user;
                ++$data['revision'];
                $this->store->write('users.json', $data);
                return self::localIdentity($user);
            }
            throw new HttpError(404, 'Usuario no encontrado.');
        });
    }
}
