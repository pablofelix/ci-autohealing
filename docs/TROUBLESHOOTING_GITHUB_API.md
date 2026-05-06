# GitHub API Error Troubleshooting

Este documento explica cómo interpretar los errores de la GitHub API durante la recolección de commit context.

## Tipos de errores comunes

### 404 - Not Found
```
DEBUG - GitHub API /repos/org/repo/commits/abc123: Not Found (404)
```

**Causas:**
- El commit SHA no existe en el repositorio (fue force-pushed, squashed, o nunca existió)
- El repositorio ha sido renombrado o eliminado
- El repositorio es privado y el token no tiene acceso

**Solución:**
- Si es un commit válido en tu base de datos, verifica que el `repository_url` es correcto
- Para repos privados, asegúrate que `GITHUB_TOKEN` tiene permisos de lectura (`repo` scope)

---

### 401 - Unauthorized
```
ERROR - GitHub API /repos/org/repo/commits/abc123: Unauthorized (401) - Token is invalid or expired
```

**Causas:**
- El `GITHUB_TOKEN` en `.env` es inválido o ha expirado
- El token fue revocado
- El formato del token es incorrecto

**Solución:**
1. Verifica que `GITHUB_TOKEN` en `.env` comienza con `ghp_` (personal access token) o `ghs_` (server token)
2. Genera un nuevo token en https://github.com/settings/tokens con scope `repo` (o `public_repo` si solo necesitas repos públicos)
3. Actualiza `.env` y reinicia el collector

---

### 403 - Forbidden

#### Caso 1: Permisos insuficientes
```
ERROR - GitHub API /repos/org/repo: Forbidden (403) - Token may lack permissions. Message: Resource protected
```

**Causas:**
- El token no tiene los scopes necesarios (`repo` para repos privados)
- El repositorio requiere SSO authorization y el token no está autorizado
- Intentas acceder a un repositorio de una organización que bloquea OAuth apps

**Solución:**
1. Verifica scopes del token: `curl -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/user` → verifica header `X-OAuth-Scopes`
2. Para repos de organizaciones con SSO: ve a https://github.com/settings/tokens, click en el token, y autoriza la organización
3. Si la org bloquea OAuth, contacta al admin para whitelist la app

#### Caso 2: Rate limit excedido
```
ERROR - GitHub API /repos/org/repo: Rate Limit Exceeded (403) - API rate limit exceeded for ...
```

**Causas:**
- Has excedido el límite de 5000 requests/hora (con token) o 60/hora (sin token)

**Solución:**
1. Verifica rate limit: `python3.11 -c "from clients.github_client import GitHubClient; from config import CollectorConfig; c = CollectorConfig.from_env(); g = GitHubClient(c.github_token); print(g.check_rate_limit())"`
2. Espera hasta el `reset` timestamp o reduce `limit` en `collect_commit_context.py`
3. Si necesitas más requests, usa un GitHub App en lugar de personal token (10000 req/hora)

---

### 429 - Too Many Requests
```
ERROR - GitHub API /repos/org/repo: Rate Limited (429) - Too many requests
```

Idéntico a 403 rate limit. Espera o reduce la frecuencia de requests.

---

### 422 - Unprocessable Entity
```
WARNING - GitHub API /repos/org/repo/commits/invalid_sha returned 422
```

**Causas:**
- El commit SHA tiene un formato inválido (no es un SHA-1 de 40 caracteres hex)
- El SHA es válido pero no existe en el repositorio

**Solución:**
- Verifica que el `commit_sha` en la base de datos es correcto
- Puede ocurrir si la pipeline falló antes de hacer git checkout (no hay commit real)

---

## Monitoreo de errores

### Ver resumen de errores en logs recientes
```bash
# Últimos errores 403/401
grep -E '(Forbidden|Unauthorized)' logs/cron/collect-comprehensive-*.log | tail -20

# Contar errores por tipo
grep -oE '(404|401|403|422|429)' logs/cron/collect-comprehensive-*.log | sort | uniq -c

# Ver qué repos dan problemas
grep 'Forbidden\|Unauthorized' logs/cron/collect-comprehensive-*.log | grep -oE 'repos/[^/]+/[^/]+' | sort | uniq -c
```

### Verificar coverage actual
```sql
SELECT 
  COUNT(*) FILTER (WHERE commit_context IS NOT NULL) as with_context,
  COUNT(*) FILTER (WHERE commit_context IS NULL) as without_context
FROM build_failures 
WHERE commit_sha IS NOT NULL;
```

---

## Cuándo considerar git clone como fallback

Si ves patrones como:
- >10 failures con 403 "Forbidden" para repos específicos
- Repos críticos que consistentemente dan 401/403
- El `GITHUB_TOKEN` no puede obtener los permisos necesarios

Entonces puede valer la pena implementar un fallback vía `git clone`. Ver propuesta en `docs/design/git-clone-fallback.md` (si existe).

Caso contrario, la GitHub API es más eficiente y fácil de mantener.
