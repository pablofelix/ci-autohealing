import dagre from 'dagre';

const NODE_WIDTH = 220;
const NODE_HEIGHT = 80;

export function layoutGraph(nodes, edges) {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'TB', nodesep: 60, ranksep: 100, edgesep: 30 });

  nodes.forEach((node) => {
    const isRelease = node.data?.category === 'release-lifecycle';
    const w = isRelease ? NODE_WIDTH + 20 : NODE_WIDTH;
    const h = isRelease ? NODE_HEIGHT + 10 : NODE_HEIGHT;
    g.setNode(node.id, { width: w, height: h });
  });

  edges.forEach((edge) => {
    g.setEdge(edge.source, edge.target);
  });

  dagre.layout(g);

  return nodes.map((node) => {
    const pos = g.node(node.id);
    return {
      ...node,
      position: {
        x: pos.x - NODE_WIDTH / 2,
        y: pos.y - NODE_HEIGHT / 2,
      },
    };
  });
}
