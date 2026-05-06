# Cómo Usar el MCP Server de Konflux

## 3 Formas de Usar las Herramientas

### 1️⃣ Llamada Directa en Python (Lo que acabamos de hacer)

```python
import asyncio
from src.konflux_mcp.tools import list_applications, get_stats

async def main():
    apps = await list_applications()
    print(apps)

asyncio.run(main())
```

**Uso:** Testing, scripts, debugging
**Ventaja:** Control total, fácil debugging
**Desventaja:** No es vía MCP protocol

---

### 2️⃣ Vía MCP Protocol (Claude Desktop)

**Ya configurado en:** `~/.config/claude/claude_desktop_config.json`

**Pasos:**
1. Reiniciar Claude Desktop (si está abierto)
2. El servidor MCP se carga automáticamente
3. En Claude Desktop, puedes preguntar:
   - "¿Qué versiones de RHOAI tenemos?"
   - "Dame un resumen de fallos en acme-v2-0"
   - "Exporta el fallo odh-vllm-cpu-v3-4 como Jira ticket"

**Claude Desktop automáticamente llamará las herramientas MCP relevantes**

---

### 3️⃣ Vía Claude Code (Esta Sesión)

Para que YO (Claude Code) pueda usar tus herramientas MCP, necesitas:

**Opción A: Preguntarme directamente sobre datos**

Simplemente pregunta cosas como:
- "¿Qué aplicaciones hay en Konflux?" → Yo llamaré `list_applications()`
- "Dame stats de acme-v2-0" → Yo llamaré `get_stats()`
- "Busca fallos con análisis AI" → Yo llamaré `search_failures()`

**Yo automáticamente usaré las herramientas si están disponibles**

**Opción B: Pedirme explícitamente que use una herramienta**

Ejemplo:
```
User: "Usa la herramienta list_applications para ver qué versiones tenemos"
```

Yo ejecutaré:
```python
from src.konflux_mcp.tools import list_applications
apps = await list_applications()
```

---

## Demo: Pruébame Ahora

Puedes preguntarme cosas como:

1. **"¿Qué versiones de RHOAI hay disponibles?"**
   → Usaré `list_applications()`

2. **"Muéstrame el estado de acme-v2-1-ea-1"**
   → Usaré `get_stats("acme-v2-1-ea-1")`

3. **"Busca fallos en acme-v2-0 que ya tengan análisis AI"**
   → Usaré `search_failures("acme-v2-0", has_analysis=True)`

4. **"Exporta acme-autorag-v3-4 como Jira ticket"**
   → Usaré `export_jira("acme-autorag-v3-4")`

5. **"Compara stats entre todas las versiones"**
   → Usaré `list_applications()` + `get_stats()` en paralelo

---

## Diferencia MCP Server vs Llamada Directa

| Aspecto | MCP Protocol | Llamada Directa Python |
|---------|-------------|------------------------|
| **Transporte** | stdio (JSON-RPC) | Importación directa |
| **Cliente** | Claude Desktop, Claude Code | Scripts Python |
| **Descubrimiento** | Automático (list tools) | Manual (conoces las funciones) |
| **Uso** | Preguntas naturales | Código explícito |
| **Validación** | Pydantic schemas | Pydantic schemas |
| **Mejor para** | Agentes AI, users | Testing, CI/CD |

---

## Herramientas Disponibles (11 total)

### Core (7)
- `list_applications()` - Ver versiones disponibles
- `list_alerts(app)` - Todos los alerts (build + conforma)
- `get_failure(component, app)` - Detalles de build failure
- `get_violation(component, app)` - Detalles de Conforma violation
- `get_analysis(component, app)` - AI analysis si existe
- `search_failures(app, category, has_analysis)` - Buscar/filtrar
- `get_stats(app)` - Resumen de stats

### Export (4)
- `export_jira(component, app)` - Jira markup
- `export_markdown(component, app)` - GitHub Issues
- `export_json(component, app)` - JSON estructurado
- `export_slack(component, app)` - Slack Block Kit

---

## Testing Rápido

```bash
# Test 1: Herramientas en Python
python3.11 test_simple.py

# Test 2: Suite completa
python3.11 test_all_tools.py

# Test 3: MCP protocol (requiere cliente MCP)
python3.11 -m konflux_mcp  # Servidor stdio esperando comandos
```

---

## Próximos Pasos

1. **Probar ahora:** Hazme una pregunta sobre Konflux y veré si puedo usar las herramientas
2. **Claude Desktop:** Abre Claude Desktop y pregunta lo mismo - debería usar MCP automáticamente
3. **Comparar:** Ve la diferencia entre cómo respondo yo (con acceso directo a Python) vs Claude Desktop (vía MCP)

¡Pregúntame algo sobre Konflux para probar! 🚀
