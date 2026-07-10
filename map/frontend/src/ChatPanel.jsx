import React, { useState, useRef, useEffect } from 'react';
import { api } from './api';

const ACTION_COLORS = {
  rebuild: { bg: '#f97316', confirm: '#ea580c' },
  triage: { bg: '#8b5cf6', confirm: '#7c3aed' },
  improvement: { bg: '#0891b2', confirm: '#0e7490' },
};

function ActionButton({ action, msgIndex, actionStates, setActionStates, onAction }) {
  const stateKey = `${msgIndex}-${action.type}-${action.params?.component || ''}`;
  const state = actionStates[stateKey] || 'idle';
  const colors = ACTION_COLORS[action.type] || ACTION_COLORS.improvement;

  if (action.confirmation === null) {
    return (
      <span
        style={{
          display: 'inline-block',
          marginTop: 4,
          marginRight: 4,
          background: '#f3f4f6',
          border: '1px solid #d1d5db',
          borderRadius: 10,
          padding: '2px 8px',
          fontSize: 10,
          color: '#4b5563',
        }}
        title={action.description}
      >
        {action.description?.slice(0, 80) || action.label}
      </span>
    );
  }

  const labels = {
    idle: action.label,
    confirming: action.confirmation || `Confirm ${action.label}?`,
    executing: 'Working...',
    done: 'Done',
    error: 'Failed — retry?',
  };

  async function handleClick() {
    if (state === 'executing' || state === 'done') return;

    if (state === 'confirming') {
      setActionStates((prev) => ({ ...prev, [stateKey]: 'executing' }));
      try {
        await onAction(action);
        setActionStates((prev) => ({ ...prev, [stateKey]: 'done' }));
      } catch {
        setActionStates((prev) => ({ ...prev, [stateKey]: 'error' }));
      }
    } else if (state === 'error') {
      setActionStates((prev) => ({ ...prev, [stateKey]: 'confirming' }));
    } else {
      setActionStates((prev) => ({ ...prev, [stateKey]: 'confirming' }));
    }
  }

  function handleCancel(e) {
    e.stopPropagation();
    setActionStates((prev) => ({ ...prev, [stateKey]: 'idle' }));
  }

  const isConfirming = state === 'confirming';
  const isDone = state === 'done';

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 2 }}>
      <button
        onClick={handleClick}
        disabled={state === 'executing' || isDone}
        title={action.description}
        style={{
          display: 'inline-block',
          marginTop: 4,
          background: isDone ? '#16a34a' : isConfirming ? colors.confirm : colors.bg,
          color: '#fff',
          border: 'none',
          borderRadius: 10,
          padding: '2px 8px',
          fontSize: 10,
          cursor: state === 'executing' || isDone ? 'default' : 'pointer',
          opacity: state === 'executing' ? 0.7 : 1,
        }}
      >
        {labels[state]}
      </button>
      {isConfirming && (
        <button
          onClick={handleCancel}
          style={{
            display: 'inline-block',
            marginTop: 4,
            background: 'none',
            border: '1px solid #d1d5db',
            borderRadius: 10,
            padding: '2px 6px',
            fontSize: 10,
            color: '#9ca3af',
            cursor: 'pointer',
          }}
        >
          Cancel
        </button>
      )}
    </span>
  );
}

export default function ChatPanel({ selectedNodeId, onHighlight, onAction }) {
  const [collapsed, setCollapsed] = useState(true);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [actionStates, setActionStates] = useState({});
  const listRef = useRef(null);

  useEffect(() => {
    if (!collapsed && listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [messages, collapsed]);

  async function handleSend(e) {
    e.preventDefault();
    const text = input.trim();
    if (!text || loading) return;

    const userMsg = { role: 'user', content: text };
    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    setLoading(true);

    try {
      const result = await api.chat(text, selectedNodeId);
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: result.response,
          model: result.model,
          highlight: result.highlight || null,
          actions: result.actions || null,
        },
      ]);
      if (result.highlight && onHighlight) {
        onHighlight(result.highlight);
      }
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: 'error', content: err.message || 'Chat unavailable' },
      ]);
    } finally {
      setLoading(false);
    }
  }

  if (collapsed) {
    return (
      <button
        onClick={() => setCollapsed(false)}
        style={{
          position: 'absolute',
          bottom: 16,
          left: 16,
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
        Ask Map
      </button>
    );
  }

  return (
    <div
      style={{
        position: 'absolute',
        bottom: 16,
        left: 16,
        width: 380,
        height: 420,
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
          <span style={{ fontSize: 12, fontWeight: 600, color: '#374151' }}>
            Map Assistant
          </span>
          {selectedNodeId && (
            <span
              style={{
                fontSize: 10,
                color: '#6b7280',
                background: '#f3f4f6',
                borderRadius: 8,
                padding: '1px 6px',
              }}
            >
              {selectedNodeId}
            </span>
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

      {/* Messages */}
      <div ref={listRef} style={{ flex: 1, overflow: 'auto', padding: 8 }}>
        {messages.length === 0 && (
          <div style={{ padding: 12, color: '#9ca3af', fontSize: 12 }}>
            <div style={{ marginBottom: 8, textAlign: 'center' }}>
              {selectedNodeId
                ? `Ask about ${selectedNodeId}`
                : 'Ask about the RHOAI CI/CD infrastructure.'}
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, justifyContent: 'center' }}>
              {(selectedNodeId
                ? [
                    'Why is this failing?',
                    'What does this do?',
                    'Who is working on this?',
                    'Show downstream impact',
                  ]
                : [
                    'Summarize current state',
                    'What needs attention?',
                    'Are we ready to release?',
                    'Explain Conforma',
                    'How does nudging work?',
                    'Walk me through a release',
                  ]
              ).map((suggestion) => (
                <button
                  key={suggestion}
                  onClick={() => { setInput(suggestion); }}
                  style={{
                    background: '#f3f4f6',
                    border: '1px solid #e5e7eb',
                    borderRadius: 12,
                    padding: '4px 10px',
                    fontSize: 11,
                    color: '#4b5563',
                    cursor: 'pointer',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((msg, i) => (
          <div
            key={i}
            style={{
              marginBottom: 6,
              padding: '6px 10px',
              borderRadius: 6,
              fontSize: 12,
              lineHeight: 1.5,
              maxWidth: '90%',
              wordBreak: 'break-word',
              ...(msg.role === 'user'
                ? {
                    marginLeft: 'auto',
                    background: '#2563eb',
                    color: '#fff',
                  }
                : msg.role === 'error'
                ? {
                    background: '#fef2f2',
                    color: '#dc2626',
                    borderLeft: '3px solid #dc2626',
                  }
                : {
                    background: '#f3f4f6',
                    color: '#374151',
                  }),
            }}
          >
            {msg.content}
            {msg.highlight && (
              <button
                onClick={() => onHighlight && onHighlight(msg.highlight)}
                style={{
                  display: 'inline-block',
                  marginTop: 4,
                  background: 'none',
                  border: '1px solid #d1d5db',
                  borderRadius: 10,
                  padding: '2px 8px',
                  fontSize: 10,
                  color: '#6b7280',
                  cursor: 'pointer',
                }}
              >
                Show on map
              </button>
            )}
            {msg.actions && msg.actions.length > 0 && (
              <div style={{ marginTop: 4, display: 'flex', flexWrap: 'wrap', gap: 2 }}>
                {msg.actions.map((action, actionIdx) => (
                  <ActionButton
                    key={actionIdx}
                    action={action}
                    msgIndex={i}
                    actionStates={actionStates}
                    setActionStates={setActionStates}
                    onAction={onAction}
                  />
                ))}
              </div>
            )}
          </div>
        ))}
        {loading && (
          <div
            style={{
              padding: '6px 10px',
              background: '#f3f4f6',
              borderRadius: 6,
              fontSize: 12,
              color: '#9ca3af',
              maxWidth: '90%',
            }}
          >
            Thinking...
          </div>
        )}
      </div>

      {/* Input */}
      <form
        onSubmit={handleSend}
        style={{
          padding: 8,
          borderTop: '1px solid #e5e7eb',
          display: 'flex',
          gap: 6,
        }}
      >
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={selectedNodeId ? `Ask about ${selectedNodeId}...` : 'Ask about the map...'}
          disabled={loading}
          style={{
            flex: 1,
            border: '1px solid #d1d5db',
            borderRadius: 6,
            padding: '6px 10px',
            fontSize: 12,
            outline: 'none',
          }}
        />
        <button
          type="submit"
          disabled={loading || !input.trim()}
          style={{
            background: loading ? '#9ca3af' : '#2563eb',
            color: '#fff',
            border: 'none',
            borderRadius: 6,
            padding: '6px 14px',
            fontSize: 12,
            cursor: loading ? 'default' : 'pointer',
            fontWeight: 600,
          }}
        >
          Send
        </button>
      </form>
    </div>
  );
}
