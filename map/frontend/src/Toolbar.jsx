import React, { useState } from 'react';

const NODE_TYPES = [
  'Application', 'Component', 'Repository', 'Pipeline',
  'TektonTask', 'Workflow', 'Automation', 'ECPolicy',
];

export default function Toolbar({ onSearch, onFilter, gapCount, stats, icAvailable }) {
  const [query, setQuery] = useState('');
  const [activeFilter, setActiveFilter] = useState(null);

  function handleSearch(e) {
    e.preventDefault();
    onSearch(query);
  }

  function handleFilter(type) {
    const next = activeFilter === type ? null : type;
    setActiveFilter(next);
    onFilter(next);
  }

  return (
    <div
      style={{
        position: 'absolute',
        top: 0,
        left: 0,
        right: 0,
        background: '#fff',
        borderBottom: '1px solid #e5e7eb',
        padding: '8px 16px',
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        zIndex: 10,
        flexWrap: 'wrap',
      }}
    >
      <h1 style={{ fontSize: 16, fontWeight: 700, margin: 0, whiteSpace: 'nowrap' }}>
        RHOAI System Map
      </h1>

      <form onSubmit={handleSearch} style={{ display: 'flex', gap: 4 }}>
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search nodes..."
          style={{
            border: '1px solid #d1d5db',
            borderRadius: 4,
            padding: '4px 8px',
            fontSize: 13,
            width: 180,
          }}
        />
        <button
          type="submit"
          style={{
            background: '#2563eb',
            color: '#fff',
            border: 'none',
            borderRadius: 4,
            padding: '4px 10px',
            fontSize: 12,
            cursor: 'pointer',
          }}
        >
          Search
        </button>
      </form>

      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
        {NODE_TYPES.map((type) => (
          <button
            key={type}
            onClick={() => handleFilter(type)}
            style={{
              background: activeFilter === type ? '#2563eb' : '#f3f4f6',
              color: activeFilter === type ? '#fff' : '#374151',
              border: 'none',
              borderRadius: 12,
              padding: '2px 10px',
              fontSize: 11,
              cursor: 'pointer',
            }}
          >
            {type}
          </button>
        ))}
      </div>

      {gapCount > 0 && (
        <span
          style={{
            background: '#fef3c7',
            color: '#92400e',
            borderRadius: 12,
            padding: '2px 10px',
            fontSize: 11,
            fontWeight: 600,
          }}
        >
          {gapCount} issue{gapCount !== 1 ? 's' : ''} detected
        </span>
      )}

      {icAvailable !== undefined && (
        <span
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 4,
            fontSize: 11,
            color: icAvailable ? '#059669' : '#9ca3af',
          }}
          title={icAvailable ? 'IC API connected — live status active' : 'IC API unavailable'}
        >
          <span style={{
            width: 7, height: 7, borderRadius: '50%',
            background: icAvailable ? '#10b981' : '#d1d5db',
          }} />
          {icAvailable ? 'Live' : 'Offline'}
        </span>
      )}

      {stats && (
        <span style={{ fontSize: 11, color: '#9ca3af', marginLeft: 'auto' }}>
          {stats.totalNodes} nodes, {stats.totalEdges} edges
        </span>
      )}
    </div>
  );
}
