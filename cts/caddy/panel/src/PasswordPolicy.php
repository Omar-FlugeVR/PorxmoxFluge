<?php
declare(strict_types=1);
namespace Homelab;

/** New local passwords use Unicode characters; existing hashes remain verifiable. */
final class PasswordPolicy
{
    public static function validate(mixed $password): string
    {
        $message = 'La contraseña debe tener entre 8 y 128 caracteres e incluir una minúscula, una mayúscula y un número.';
        if (!is_string($password) || strlen($password) > 1024 || str_contains($password, "\0")) {
            throw new HttpError(422, $message);
        }
        $length = preg_match_all('/./us', $password);
        if ($length === false || $length < 8 || $length > 128 || !preg_match('/\p{Ll}/u', $password) || !preg_match('/\p{Lu}/u', $password) || !preg_match('/\p{N}/u', $password)) {
            throw new HttpError(422, $message);
        }
        return $password;
    }

    public static function confirmed(mixed $password, mixed $confirmation): string
    {
        $password = self::validate($password);
        if (!is_string($confirmation) || !hash_equals($password, $confirmation)) {
            throw new HttpError(422, 'Las contraseñas no coinciden.');
        }
        return $password;
    }

    public static function hash(string $password): string
    {
        self::validate($password);
        if (!defined('PASSWORD_ARGON2ID')) {
            throw new HttpError(503, 'El servidor no permite guardar nuevas contraseñas en este momento.');
        }
        return password_hash($password, PASSWORD_ARGON2ID, ['memory_cost' => 32768, 'time_cost' => 2, 'threads' => 1]);
    }

    public static function verify(string $password, string $hash): bool
    {
        if (strlen($password) > 1024 || str_contains($password, "\0")) {
            return false;
        }
        // Bcrypt ignores the suffix after its historical limit; never accept that suffix.
        if (password_get_info($hash)['algoName'] === 'bcrypt' && strlen($password) > 72) {
            return false;
        }
        return password_verify($password, $hash);
    }
}
