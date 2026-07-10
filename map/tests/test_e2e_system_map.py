"""E2E BDD tests for the System Map UI.

These tests run against the real map backend (mocked Neo4j via graph module patch).
They validate the data contracts the React frontend depends on:
- Graph format (React Flow nodes/edges)
- Node detail with external URLs
- Search, filtering, gaps, stats, path finding, health
"""

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from map.tests.conftest_e2e import map_client  # noqa: F401 — BDD fixture

scenarios('features/system_map.feature')

pytestmark = pytest.mark.e2e


# ==============================================================================
# Background
# ==============================================================================

@given('the system map backend is running')
def backend_running(map_client):
    resp = map_client.get('/api/map/health')
    assert resp.status_code == 200


@given('the graph is seeded with infrastructure data')
def graph_seeded(map_client):
    resp = map_client.get('/api/map/stats')
    data = resp.json()
    total = sum(n['count'] for n in data['nodes'])
    assert total > 0


# ==============================================================================
# Graph loading
# ==============================================================================

@when('I request the full graph', target_fixture='graph_response')
def request_full_graph(map_client):
    resp = map_client.get('/api/map/graph')
    assert resp.status_code == 200
    return resp.json()


@then('I receive nodes and edges in React Flow format')
def check_react_flow_format(graph_response):
    assert 'nodes' in graph_response
    assert 'edges' in graph_response
    assert len(graph_response['nodes']) > 0
    assert len(graph_response['edges']) > 0


@then('each node has an id, type, and data object')
def check_node_structure(graph_response):
    for node in graph_response['nodes']:
        assert 'id' in node
        assert 'type' in node
        assert 'data' in node
        assert 'label' in node['data']
        assert 'nodeType' in node['data']


@then('each edge has source, target, and label')
def check_edge_structure(graph_response):
    for edge in graph_response['edges']:
        assert 'source' in edge
        assert 'target' in edge
        assert 'label' in edge


@then(parsers.parse('the graph contains nodes of type "{node_type}"'))
def graph_contains_type(graph_response, node_type):
    types = {n['type'] for n in graph_response['nodes']}
    assert node_type in types, f"Expected type '{node_type}' in {types}"


# ==============================================================================
# Node detail + external links
# ==============================================================================

@given(parsers.parse('a repository node "{node_id}" exists in the graph'))
@given(parsers.parse('a workflow node "{node_id}" exists in the graph'))
@given(parsers.parse('an EC policy node "{node_id}" exists in the graph'))
@given(parsers.parse('a pipeline node "{node_id}" exists in the graph'))
@given(parsers.parse('a Tekton task node "{node_id}" exists in the graph'))
@given(parsers.parse('an automation node "{node_id}" exists in the graph'))
def node_exists(node_id, map_client):
    resp = map_client.get(f'/api/map/node/{node_id}')
    assert resp.status_code == 200, f"Node '{node_id}' not found"


@when(parsers.parse('I request the node detail for "{node_id}"'), target_fixture='detail_response')
def request_node_detail(node_id, map_client):
    resp = map_client.get(f'/api/map/node/{node_id}')
    assert resp.status_code == 200
    return resp.json()


@then(parsers.parse('the detail panel shows the node type "{expected_type}"'))
def detail_shows_type(detail_response, expected_type):
    assert detail_response['type'] == expected_type


@then(parsers.parse('the detail includes a "{prop}" property'))
def detail_includes_prop(detail_response, prop):
    assert prop in detail_response.get('props', {}), \
        f"Expected '{prop}' in props: {list(detail_response.get('props', {}).keys())}"


@then(parsers.parse('the url starts with "{prefix}"'))
def url_starts_with(detail_response, prefix):
    url = detail_response['props'].get('url', '')
    assert url.startswith(prefix), f"URL '{url}' does not start with '{prefix}'"


@then(parsers.parse('the url contains "{substring}"'))
def url_contains(detail_response, substring):
    url = detail_response['props'].get('url', '')
    assert substring in url, f"URL '{url}' does not contain '{substring}'"


@then('the detail does not contain internal properties')
def no_internal_props(detail_response):
    props = detail_response.get('props', {})
    for key in props:
        assert not key.startswith('_'), f"Internal prop '{key}' should not be exposed"


@then('the detail includes a list of neighbor connections')
def detail_has_neighbors(detail_response):
    assert 'neighbors' in detail_response
    assert len(detail_response['neighbors']) > 0


@then('each neighbor has id, type, relationship, and direction')
def neighbor_structure(detail_response):
    for n in detail_response['neighbors']:
        assert 'id' in n
        assert 'type' in n
        assert 'relationship' in n
        assert 'direction' in n
        assert n['direction'] in ('incoming', 'outgoing')


# ==============================================================================
# Search
# ==============================================================================

@when(parsers.parse('I search for "{query}"'), target_fixture='search_response')
def search_nodes(query, map_client):
    resp = map_client.get(f'/api/map/search?q={query}')
    assert resp.status_code == 200
    return resp.json()


@when(parsers.parse('I search for "{query}" with type filter "{type_filter}"'), target_fixture='search_response')
def search_nodes_with_type(query, type_filter, map_client):
    resp = map_client.get(f'/api/map/search?q={query}&type={type_filter}')
    assert resp.status_code == 200
    return resp.json()


@then('I receive search results')
def has_search_results(search_response):
    assert search_response['count'] > 0


@then(parsers.parse('the results include a node matching "{substring}"'))
def results_match(search_response, substring):
    found = any(
        substring.lower() in (r.get('name') or r.get('id') or '').lower()
        for r in search_response['results']
    )
    assert found, f"No result matching '{substring}'"


@then('the search returns zero results')
def search_empty(search_response):
    assert search_response['count'] == 0


@then(parsers.parse('all results have type "{expected_type}"'))
def results_all_type(search_response, expected_type):
    for r in search_response['results']:
        assert r['type'] == expected_type, f"Expected type '{expected_type}', got '{r['type']}'"


# ==============================================================================
# Type filtering (client-side logic, but validate data supports it)
# ==============================================================================

@when(parsers.parse('I filter for type "{node_type}"'))
def filter_by_type(graph_response, node_type):
    visible = set()
    for n in graph_response['nodes']:
        if n['data']['nodeType'] == node_type:
            visible.add(n['id'])
    for e in graph_response['edges']:
        if e['source'] in visible:
            visible.add(e['target'])
        if e['target'] in visible:
            visible.add(e['source'])
    graph_response['_visible'] = visible
    graph_response['_filter_type'] = node_type


@then('only repository nodes and their direct connections are visible')
def check_filtered_visibility(graph_response):
    visible = graph_response['_visible']
    assert len(visible) > 0
    for n in graph_response['nodes']:
        if n['id'] in visible and n['data']['nodeType'] == 'Repository':
            continue
        if n['id'] in visible:
            connected = any(
                (e['source'] == n['id'] or e['target'] == n['id'])
                and (e['source'] in visible or e['target'] in visible)
                for e in graph_response['edges']
            )
            assert connected, f"Non-repo node '{n['id']}' is visible but not connected to a repo"


# ==============================================================================
# Gaps
# ==============================================================================

@when('I request the infrastructure gaps', target_fixture='gaps_response')
def request_gaps(map_client):
    resp = map_client.get('/api/map/gaps')
    assert resp.status_code == 200
    return resp.json()


@then('I receive a structured gap report')
def structured_gap_report(gaps_response):
    assert 'gaps' in gaps_response
    assert 'count' in gaps_response
    assert isinstance(gaps_response['gaps'], list)


@then('each gap has node_id, type, severity, and message')
def gap_structure(gaps_response):
    for g in gaps_response['gaps']:
        assert 'node_id' in g
        assert 'type' in g
        assert 'severity' in g
        assert 'message' in g
        assert g['severity'] in ('error', 'warning', 'info')


# ==============================================================================
# Statistics
# ==============================================================================

@when('I request the graph statistics', target_fixture='stats_response')
def request_stats(map_client):
    resp = map_client.get('/api/map/stats')
    assert resp.status_code == 200
    return resp.json()


@then('I receive node counts grouped by type')
def node_counts_by_type(stats_response):
    assert 'nodes' in stats_response
    assert len(stats_response['nodes']) > 0
    for entry in stats_response['nodes']:
        assert 'type' in entry
        assert 'count' in entry
        assert entry['count'] > 0


@then('I receive edge counts grouped by type')
def edge_counts_by_type(stats_response):
    assert 'edges' in stats_response
    assert len(stats_response['edges']) > 0
    for entry in stats_response['edges']:
        assert 'type' in entry
        assert 'count' in entry


# ==============================================================================
# Path finding
# ==============================================================================

@when(parsers.parse('I find the path from "{from_id}" to "{to_id}"'), target_fixture='path_response')
def find_path(from_id, to_id, map_client):
    resp = map_client.get(f'/api/map/path/{from_id}/{to_id}')
    return {'status_code': resp.status_code, 'data': resp.json()}


@then('I receive a path with nodes and edges')
def path_has_nodes_edges(path_response):
    assert path_response['status_code'] == 200
    data = path_response['data']
    assert 'nodes' in data
    assert 'edges' in data
    assert len(data['nodes']) >= 2


@then(parsers.parse('the path starts at "{start_id}"'))
def path_starts_at(path_response, start_id):
    first = path_response['data']['nodes'][0]
    assert first['id'] == start_id


@then(parsers.parse('the path ends at "{end_id}"'))
def path_ends_at(path_response, end_id):
    last = path_response['data']['nodes'][-1]
    assert last['id'] == end_id


@then('I receive a 404 response')
def response_404(path_response):
    assert path_response['status_code'] == 404


# ==============================================================================
# Health
# ==============================================================================

@when('I request the health status', target_fixture='health_response')
def request_health(map_client):
    resp = map_client.get('/api/map/health')
    assert resp.status_code == 200
    return resp.json()


@then(parsers.parse('the status is "{expected}"'))
def check_status(health_response, expected):
    assert health_response['status'] == expected


@then(parsers.parse('neo4j is "{expected}"'))
def check_neo4j_status(health_response, expected):
    assert health_response['neo4j'] == expected


@then('the total node count is greater than zero')
def check_node_count(health_response):
    assert health_response['total_nodes'] > 0
