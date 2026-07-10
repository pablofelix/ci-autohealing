import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import { api } from './api';
import { layoutGraph } from './layout';
import MapNode from './nodes/MapNode';
import DetailPanel from './DetailPanel';
import Toolbar from './Toolbar';
import ActivityFeed from './ActivityFeed';
import ChatPanel from './ChatPanel';
import { useLiveStatus } from './hooks/useLiveStatus';

const nodeTypes = { default: MapNode };

const TYPE_MINIMAP_COLORS = {
  Application: '#2563eb',
  Component: '#059669',
  Repository: '#7c3aed',
  Pipeline: '#d97706',
  TektonTask: '#dc2626',
  Workflow: '#0891b2',
  Automation: '#4f46e5',
  ECPolicy: '#be185d',
};

export default function App() {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [allNodes, setAllNodes] = useState([]);
  const [allEdges, setAllEdges] = useState([]);
  const [selectedNode, setSelectedNode] = useState(null);
  const [gapCount, setGapCount] = useState(0);
  const [stats, setStats] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  const { statusMap, onboardingMap, activity, icAvailable } = useLiveStatus('rhoai-v3-5');

  // Parse ?highlight= URL param for deep-linking from CLI
  const [highlightId] = useState(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get('highlight') || null;
  });

  // Fetch graph data on mount
  useEffect(() => {
    loadGraph();
  }, []);

  // Auto-select highlighted node from URL param once graph loads (fire once)
  const highlightApplied = useRef(false);
  useEffect(() => {
    if (!highlightId || allNodes.length === 0 || highlightApplied.current) return;
    const match = allNodes.find((n) => n.id === highlightId);
    if (match) {
      highlightApplied.current = true;
      setSelectedNode(match.id);
      setNodes((nds) =>
        nds.map((n) => ({
          ...n,
          selected: n.id === match.id,
          style: n.id === match.id ? { opacity: 1 } : { opacity: 0.35 },
        }))
      );
    }
  }, [highlightId, allNodes, setNodes]);

  // Merge live status and onboarding into nodes
  useEffect(() => {
    if (statusMap.size === 0 && onboardingMap.size === 0) return;
    setNodes((prev) => {
      if (prev.length === 0) return prev;
      return prev.map((n) => {
        const live = statusMap.get(n.id);
        const onboarding = onboardingMap.get(n.id);
        if (!live && !onboarding) return n;
        const data = { ...n.data };
        if (live && live.border_color) data.liveStatus = live;
        if (onboarding) data.onboarding = onboarding;
        return { ...n, data };
      });
    });
  }, [statusMap, onboardingMap, setNodes]);

  async function loadGraph() {
    setLoading(true);
    setError(null);
    try {
      const data = await api.graph();
      const laidOut = layoutGraph(data.nodes, data.edges);

      // Convert to React Flow node type
      const rfNodes = laidOut.map((n) => ({
        ...n,
        type: 'default',
      }));

      setAllNodes(rfNodes);
      setAllEdges(data.edges);
      setNodes(rfNodes);
      setEdges(data.edges);

      const gapNodes = rfNodes.filter((n) => n.data.hasGaps);
      setGapCount(gapNodes.reduce((sum, n) => sum + n.data.gaps.length, 0));

      setStats({
        totalNodes: rfNodes.length,
        totalEdges: data.edges.length,
      });
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  const onNodeClick = useCallback((_, node) => {
    setSelectedNode(node.id);
  }, []);

  const handleSearch = useCallback(
    async (query) => {
      if (!query.trim()) {
        setNodes(allNodes);
        setEdges(allEdges);
        return;
      }
      try {
        const data = await api.search(query);
        const matchIds = new Set(data.results.map((r) => r.id));

        // Highlight matching nodes, dim others
        setNodes(
          allNodes.map((n) => ({
            ...n,
            style: matchIds.has(n.id)
              ? { opacity: 1 }
              : { opacity: 0.15 },
          }))
        );
      } catch {
        // keep current view on search error
      }
    },
    [allNodes, allEdges, setNodes, setEdges]
  );

  const handleFilter = useCallback(
    (type) => {
      if (!type) {
        setNodes(allNodes);
        setEdges(allEdges);
        return;
      }

      const visible = new Set();
      allNodes.forEach((n) => {
        if (n.data.nodeType === type) visible.add(n.id);
      });

      // Also show directly connected nodes
      allEdges.forEach((e) => {
        if (visible.has(e.source)) visible.add(e.target);
        if (visible.has(e.target)) visible.add(e.source);
      });

      setNodes(
        allNodes.map((n) => ({
          ...n,
          style: visible.has(n.id) ? { opacity: 1 } : { opacity: 0.1 },
        }))
      );
      setEdges(
        allEdges.map((e) => ({
          ...e,
          style:
            visible.has(e.source) && visible.has(e.target)
              ? { opacity: 1 }
              : { opacity: 0.05 },
        }))
      );
    },
    [allNodes, allEdges, setNodes, setEdges]
  );

  const handleNavigate = useCallback(
    (nodeId) => {
      setSelectedNode(nodeId);
      const node = allNodes.find((n) => n.id === nodeId);
      if (node) {
        setNodes((nds) =>
          nds.map((n) => ({
            ...n,
            selected: n.id === nodeId,
          }))
        );
      }
    },
    [allNodes, setNodes]
  );

  const handleImpact = useCallback(
    async (nodeId, direction = 'downstream') => {
      try {
        const data = await api.impact(nodeId, { direction });
        const affectedIds = new Set(data.affected.map((a) => a.id));
        affectedIds.add(nodeId);

        setNodes(
          allNodes.map((n) => ({
            ...n,
            style: affectedIds.has(n.id)
              ? { opacity: 1, boxShadow: n.id === nodeId ? '0 0 0 3px #dc2626' : '0 0 0 2px #f97316' }
              : { opacity: 0.12 },
          }))
        );
        setEdges(
          allEdges.map((e) => ({
            ...e,
            style:
              affectedIds.has(e.source) && affectedIds.has(e.target)
                ? { opacity: 1, stroke: '#f97316', strokeWidth: 2 }
                : { opacity: 0.05 },
          }))
        );
      } catch {
        // keep current view on impact error
      }
    },
    [allNodes, allEdges, setNodes, setEdges]
  );

  const clearHighlight = useCallback(() => {
    setNodes(allNodes.map((n) => ({ ...n, style: undefined })));
    setEdges(allEdges.map((e) => ({ ...e, style: undefined })));
  }, [allNodes, allEdges, setNodes, setEdges]);

  const handleChatHighlight = useCallback(
    (highlight) => {
      if (!highlight) {
        clearHighlight();
        return;
      }
      const nodeSet = new Set(highlight.nodes || []);
      const edgeSet = new Set(highlight.edges || []);
      const glow = highlight.glow_color || '#f97316';

      setNodes(
        allNodes.map((n) => ({
          ...n,
          style: nodeSet.has(n.id)
            ? { opacity: 1, boxShadow: `0 0 0 2px ${glow}` }
            : highlight.dim_others !== false
            ? { opacity: 0.12 }
            : { opacity: 1 },
        }))
      );
      setEdges(
        allEdges.map((e) => ({
          ...e,
          style:
            edgeSet.has(e.id) || (nodeSet.has(e.source) && nodeSet.has(e.target))
              ? { opacity: 1, stroke: glow, strokeWidth: 2 }
              : { opacity: 0.05 },
        }))
      );
    },
    [allNodes, allEdges, setNodes, setEdges, clearHighlight]
  );

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh' }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 24, marginBottom: 8 }}>Loading System Map...</div>
          <div style={{ color: '#9ca3af' }}>Connecting to Neo4j</div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh' }}>
        <div style={{ textAlign: 'center', maxWidth: 400 }}>
          <div style={{ fontSize: 24, marginBottom: 8, color: '#dc2626' }}>Connection Error</div>
          <div style={{ color: '#6b7280', marginBottom: 16 }}>{error}</div>
          <div style={{ fontSize: 13, color: '#9ca3af' }}>
            Make sure the backend is running:
            <pre style={{ background: '#f3f4f6', padding: 8, borderRadius: 4, marginTop: 8 }}>
              cd map && uvicorn backend.main:app --port 8081
            </pre>
          </div>
          <button
            onClick={loadGraph}
            style={{
              marginTop: 16,
              background: '#2563eb',
              color: '#fff',
              border: 'none',
              borderRadius: 6,
              padding: '8px 20px',
              cursor: 'pointer',
            }}
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div style={{ width: '100%', height: '100vh' }}>
      <Toolbar
        onSearch={handleSearch}
        onFilter={handleFilter}
        gapCount={gapCount}
        stats={stats}
        icAvailable={icAvailable}
      />

      <div style={{ width: '100%', height: 'calc(100vh - 50px)', marginTop: 50 }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={onNodeClick}
          nodeTypes={nodeTypes}
          fitView
          minZoom={0.1}
          maxZoom={2}
          attributionPosition="bottom-left"
        >
          <Background variant="dots" gap={16} size={1} color="#e5e7eb" />
          <Controls position="bottom-right" />
          <MiniMap
            nodeColor={(n) => TYPE_MINIMAP_COLORS[n.data?.nodeType] || '#9ca3af'}
            maskColor="rgba(255,255,255,0.7)"
            style={{ border: '1px solid #e5e7eb' }}
          />
        </ReactFlow>
      </div>

      <DetailPanel
        nodeId={selectedNode}
        onClose={() => setSelectedNode(null)}
        onNavigate={handleNavigate}
        onboardingMap={onboardingMap}
        onImpact={handleImpact}
        onClearHighlight={clearHighlight}
      />

      <ActivityFeed activity={activity} icAvailable={icAvailable} />
      <ChatPanel selectedNodeId={selectedNode} onHighlight={handleChatHighlight} />
    </div>
  );
}
