#!/usr/bin/env python3.11
"""Simple test to show MCP tools in action."""

import asyncio
from src.konflux_mcp.tools import list_applications, get_stats, search_failures


async def demo():
    print("🔍 Probando herramientas MCP...\n")

    # 1. Ver qué aplicaciones tenemos
    print("1. ¿Qué versiones de RHOAI tenemos?")
    apps = await list_applications()
    for app in apps:
        print(f"   - {app.name}: {app.failure_count} fallos")
    print()

    # 2. Ver stats de una versión
    print("2. ¿Cuál es el estado de acme-v2-0?")
    stats = await get_stats("acme-v2-0")
    print(f"   Build failures pendientes: {stats.build_failures['pending']}")
    print(f"   Conforma violations pendientes: {stats.conforma_violations['pending']}")
    print()

    # 3. Buscar fallos con análisis AI
    print("3. ¿Qué fallos ya tienen análisis AI?")
    failures = await search_failures("acme-v2-0", has_analysis=True, limit=3)
    for f in failures:
        print(f"   - {f.component}")
    print()

    print("✅ Herramientas funcionando correctamente!")


if __name__ == "__main__":
    asyncio.run(demo())
