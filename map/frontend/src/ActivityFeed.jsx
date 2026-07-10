import React, { useState, useRef, useEffect } from 'react';

const SEVERITY_STYLES = {
  error: { color: '#dc2626', bg: '#fef2f2' },
  warning: { color: '#d97706', bg: '#fffbeb' },
  info: { color: '#2563eb', bg: '#eff6ff' },
  pr_open: { color: '#2563eb', bg: '#eff6ff' },
  pr_merged: { color: '#059669', bg: '#ecfdf5' },
  pr_stale: { color: '#d97706', bg: '#fffbeb' },
};

const FILTER_TABS = [
  { key: 'all', label: 'All' },
  { key: 'error', label: 'Errors' },
  { key: 'warning', label: 'Warnings' },
  { key: 'info', label: 'Info' },
];

function formatTime(timestamp) {
  if (!timestamp) return '';
  try {
    return new Date(timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '';
  }
}

export default function ActivityFeed({ activity, icAvailable, onNavigate }) {
  const [collapsed, setCollapsed] = useState(false);
  const [filter, setFilter] = useState('all');
  const listRef = useRef(null);

  const filtered = filter === 'all'
    ? activity
    : activity.filter((e) => e.severity === filter);

  const errorCount = activity.filter((e) => e.severity === 'error').length;

  useEffect(() => {
    if (!collapsed && listRef.current) {
      listRef.current.scrollTop = 0;
    }
  }, [activity, collapsed]);

  if (collapsed) {
    return (
      <button
        onClick={() => setCollapsed(false)}
        style={{
          position: 'absolute',
          bottom: 16,
          right: 16,
          background: '#1e293b',
          color: '#fff',
          border: 'none',
          borderRadius: 20,
          padding: '8px 16px',
          fontSize: 12,
          fontWeight: 600,
          cursor: 'pointer',
          boxShadow: '0 2px 8px rgba(0,0,0,0.2)',
          zIndex: 10,
          display: 'flex',
          alignItems: 'center',
          gap: 6,
        }}
      >
        <span style={{
          width: 8, height: 8, borderRadius: '50%',
          background: icAvailable ? '#10b981' : '#ef4444',
        }} />
        Activity ({activity.length})
        {errorCount > 0 && (
          <span style={{
            background: '#dc2626',
            color: '#fff',
            borderRadius: 8,
            padding: '0 5px',
            fontSize: 10,
            fontWeight: 700,
            minWidth: 16,
            textAlign: 'center',
          }}>
            {errorCount}
          </span>
        )}
      </button>
    );
  }

  return (
    <div
      style={{
        position: 'absolute',
        bottom: 16,
        right: 16,
        width: 340,
        maxHeight: 360,
        background: '#fff',
        border: '1px solid #e5e7eb',
        borderRadius: 8,
        boxShadow: '0 4px 12px rgba(0,0,0,0.12)',
        zIndex: 10,
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: '8px 12px',
          background: '#f9fafb',
          borderBottom: '1px solid #e5e7eb',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{
            width: 8, height: 8, borderRadius: '50%',
            background: icAvailable ? '#10b981' : '#ef4444',
          }} />
          <span style={{ fontSize: 12, fontWeight: 600, color: '#374151' }}>
            Activity Feed
          </span>
          {!icAvailable && (
            <span style={{ fontSize: 10, color: '#ef4444' }}>(offline)</span>
          )}
        </div>
        <button
          onClick={() => setCollapsed(true)}
          style={{
            background: 'none',
            border: 'none',
            fontSize: 16,
            cursor: 'pointer',
            color: '#9ca3af',
            padding: '0 4px',
          }}
        >
          -
        </button>
      </div>

      {/* Filter tabs */}
      <div style={{
        display: 'flex',
        padding: '4px 8px',
        gap: 4,
        borderBottom: '1px solid #f3f4f6',
      }}>
        {FILTER_TABS.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setFilter(tab.key)}
            style={{
              flex: 1,
              padding: '3px 0',
              fontSize: 10,
              fontWeight: filter === tab.key ? 700 : 500,
              color: filter === tab.key ? '#1e293b' : '#9ca3af',
              background: filter === tab.key ? '#f1f5f9' : 'transparent',
              border: 'none',
              borderRadius: 4,
              cursor: 'pointer',
            }}
          >
            {tab.label}
            {tab.key === 'error' && errorCount > 0 && (
              <span style={{
                marginLeft: 3,
                background: '#dc2626',
                color: '#fff',
                borderRadius: 6,
                padding: '0 4px',
                fontSize: 9,
              }}>
                {errorCount}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Event list */}
      <div ref={listRef} style={{ flex: 1, overflow: 'auto', padding: 8 }}>
        {!icAvailable && activity.length === 0 ? (
          <div style={{ padding: 24, textAlign: 'center', color: '#9ca3af', fontSize: 12 }}>
            IC API unavailable
          </div>
        ) : filtered.length === 0 ? (
          <div style={{ padding: 24, textAlign: 'center', color: '#9ca3af', fontSize: 12 }}>
            {filter === 'all' ? 'No recent activity' : `No ${filter} events`}
          </div>
        ) : (
          filtered.map((event, i) => {
            const style = SEVERITY_STYLES[event.severity] || SEVERITY_STYLES.info;
            const compNodeId = event.component ? `comp-${event.component}` : null;
            return (
              <div
                key={`${event.component}-${event.timestamp}-${i}`}
                style={{
                  marginBottom: 6,
                  padding: '6px 8px',
                  background: style.bg,
                  borderRadius: 4,
                  borderLeft: `3px solid ${style.color}`,
                  fontSize: 11,
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}>
                  {compNodeId && onNavigate ? (
                    <span
                      onClick={() => onNavigate(compNodeId)}
                      style={{
                        fontWeight: 600,
                        color: '#2563eb',
                        cursor: 'pointer',
                        textDecoration: 'underline',
                        textDecorationColor: '#93c5fd',
                      }}
                      title={`Navigate to ${event.component}`}
                    >
                      {event.component}
                    </span>
                  ) : (
                    <span style={{ fontWeight: 600, color: '#374151' }}>
                      {event.component}
                    </span>
                  )}
                  <span style={{ color: '#9ca3af', fontSize: 10 }}>
                    {formatTime(event.timestamp)}
                  </span>
                </div>
                <div style={{ color: '#6b7280', lineHeight: 1.4 }}>
                  {event.message}
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
