import React, { useState, useRef, useEffect } from 'react';

const SEVERITY_STYLES = {
  error: { color: '#dc2626', bg: '#fef2f2' },
  warning: { color: '#d97706', bg: '#fffbeb' },
  info: { color: '#2563eb', bg: '#eff6ff' },
};

function formatTime(timestamp) {
  if (!timestamp) return '';
  try {
    return new Date(timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '';
  }
}

export default function ActivityFeed({ activity, icAvailable }) {
  const [collapsed, setCollapsed] = useState(false);
  const listRef = useRef(null);

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

      {/* Event list */}
      <div ref={listRef} style={{ flex: 1, overflow: 'auto', padding: 8 }}>
        {!icAvailable && activity.length === 0 ? (
          <div style={{ padding: 24, textAlign: 'center', color: '#9ca3af', fontSize: 12 }}>
            IC API unavailable
          </div>
        ) : activity.length === 0 ? (
          <div style={{ padding: 24, textAlign: 'center', color: '#9ca3af', fontSize: 12 }}>
            No recent activity
          </div>
        ) : (
          activity.map((event, i) => {
            const style = SEVERITY_STYLES[event.severity] || SEVERITY_STYLES.info;
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
                  <span style={{ fontWeight: 600, color: '#374151' }}>
                    {event.component}
                  </span>
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
