#!/usr/bin/env python3.11
"""Migrate all MCP tools to use @async_tool decorator.

This script automates the refactoring of async tools that use
loop.run_in_executor to use the @async_tool decorator instead.
"""

import re


def migrate_tool(content: str, tool_name: str) -> str:
    """Migrate a single tool to use @async_tool decorator.

    Pattern to match:
        @mcp.tool()
        async def tool_name(...) -> ReturnType:
            '''docstring'''
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, _sync_tool_name, ...)

        def _sync_tool_name(...) -> ReturnType:
            '''Synchronous implementation of tool_name.'''
            ...

    Transform to:
        @mcp.tool()
        @async_tool
        def tool_name(...) -> ReturnType:
            '''docstring'''
            ...
    """
    # Find the async function
    async_pattern = rf'(@mcp\.tool\(\)\s+)async def {tool_name}\((.*?)\) -> (.*?):\s+(""".*?""")\s+loop = asyncio\.get_event_loop\(\)\s+return await loop\.run_in_executor\(None, _sync_{tool_name}(?:, (.*?))?\)'

    # Find the sync function
    sync_pattern = rf'\n\ndef _sync_{tool_name}\((.*?)\) -> (.*?):\s+(""".*?""")'

    # Replace async function with decorator version
    def replace_async(match):
        decorator = match.group(1)
        params = match.group(2)
        return_type = match.group(3)
        docstring = match.group(4)

        return f'{decorator}@async_tool\ndef {tool_name}({params}) -> {return_type}:\n    {docstring}'

    content = re.sub(async_pattern, replace_async, content, flags=re.DOTALL)

    # Remove the "Synchronous implementation" line from sync function docstring
    content = re.sub(rf'(def _sync_{tool_name}.*?""")(Synchronous implementation of {tool_name}\.\s*)', r'\1', content, flags=re.DOTALL)

    # Rename _sync_tool_name to tool_name (remove _sync_ prefix)
    content = re.sub(rf'def _sync_{tool_name}\(', f'def {tool_name}(', content)

    return content


def main():
    # Read tools.py
    with open('src/konflux_mcp/tools.py', 'r') as f:
        content = f.read()

    # List of tools to migrate (already migrated: list_applications)
    tools_to_migrate = [
        'list_alerts',
        'get_failure',
        'get_violation',
        'get_analysis',
        'search_failures',
        'get_stats',
        'export_jira',
        'export_markdown',
        'export_json',
        'export_slack',
    ]

    print(f"Migrating {len(tools_to_migrate)} tools to @async_tool decorator...\n")

    for tool in tools_to_migrate:
        print(f"  ✓ Migrating {tool}()")
        content = migrate_tool(content, tool)

    # Write back
    with open('src/konflux_mcp/tools.py', 'w') as f:
        f.write(content)

    print(f"\n✅ Migration complete! All {len(tools_to_migrate)} tools migrated.")
    print("\nNext: Test with: python3.11 test_simple.py")


if __name__ == '__main__':
    main()
