import React from 'react';
import { Handle, Position } from '@xyflow/react';

const TYPE_COLORS = {
  Application: '#2563eb',
  Component: '#059669',
  Repository: '#7c3aed',
  Pipeline: '#d97706',
  TektonTask: '#dc2626',
  Workflow: '#0891b2',
  Automation: '#4f46e5',
  ECPolicy: '#be185d',
  ContainerImage: '#65a30d',
  Environment: '#6b7280',
};

const TYPE_ICONS = {
  Application: '📦',
  Component: '🔧',
  Repository: '📂',
  Pipeline: '⚙️',
  TektonTask: '🔨',
  Workflow: '🔄',
  Automation: '🤖',
  ECPolicy: '🛡️',
  ContainerImage: '🐳',
  Environment: '🌐',
};

export default function MapNode({ data, selected }) {
  const typeColor = TYPE_COLORS[data.nodeType] || '#6b7280';
  const liveStatus = data.liveStatus;
  const color = (liveStatus?.border_color) || typeColor;
  const icon = TYPE_ICONS[data.nodeType] || '●';
  const hasGaps = data.hasGaps;

  return (
    <div
      style={{
        background: '#fff',
        border: `2px solid ${selected ? '#000' : color}`,
        borderRadius: 8,
        padding: '8px 12px',
        minWidth: 180,
        maxWidth: 260,
        boxShadow: selected
          ? '0 0 0 2px rgba(0,0,0,0.2)'
          : '0 1px 3px rgba(0,0,0,0.1)',
        position: 'relative',
        cursor: 'pointer',
      }}
    >
      <Handle type="target" position={Position.Top} style={{ background: typeColor }} />

      {liveStatus && (
        <span
          style={{
            position: 'absolute',
            top: -6,
            left: -6,
            width: 12,
            height: 12,
            borderRadius: '50%',
            background: liveStatus.border_color,
            border: '2px solid #fff',
            boxShadow: '0 0 2px rgba(0,0,0,0.2)',
          }}
          title={`Health: ${liveStatus.health_score ?? 'N/A'} — ${liveStatus.status}`}
        />
      )}

      {hasGaps && (
        <span
          style={{
            position: 'absolute',
            top: -8,
            right: -8,
            background: '#f59e0b',
            color: '#fff',
            borderRadius: '50%',
            width: 20,
            height: 20,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 12,
            fontWeight: 'bold',
          }}
          title={`${data.gaps.length} issue(s) detected`}
        >
          !
        </span>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ fontSize: 16 }}>{icon}</span>
        <span
          style={{
            fontSize: 10,
            textTransform: 'uppercase',
            color,
            fontWeight: 600,
            letterSpacing: '0.05em',
          }}
        >
          {data.nodeType}
        </span>
      </div>

      <div
        style={{
          fontSize: 13,
          fontWeight: 600,
          marginTop: 4,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
        title={data.label}
      >
        {data.label}
      </div>

      {data.description && (
        <div
          style={{
            fontSize: 11,
            color: '#6b7280',
            marginTop: 2,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {data.description}
        </div>
      )}

      <Handle type="source" position={Position.Bottom} style={{ background: typeColor }} />
    </div>
  );
}
