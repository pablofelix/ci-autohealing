import React, { useEffect, useState, useCallback, useRef } from 'react';
import { api } from './api';

const SEVERITY_COLORS = { error: '#dc2626', warning: '#f59e0b', info: '#3b82f6' };

const URL_PROPS = new Set(['url', 'href', 'link', 'homepage']);

function isUrl(value) {
  return typeof value === 'string' && /^https?:\/\//.test(value);
}

function formatPropKey(key) {
  return key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

const MIN_ZOOM = 0.7;
const MAX_ZOOM = 1.6;

const CHECK_STATUS_ICONS = {
  PASS: { icon: '✓', color: '#10b981' },
  FAIL: { icon: '✗', color: '#ef4444' },
  WARN: { icon: '⚠', color: '#f59e0b' },
  INFO: { icon: 'i', color: '#3b82f6' },
  SKIP: { icon: '—', color: '#9ca3af' },
};

const CHECK_LABELS = {
  repository: 'Repository URL',
  branch: 'Release Branch',
  container_image: 'Container Image',
  pac: 'PipelinesAsCode',
  builds: 'Build Status',
  last_built: 'Last Built',
  nudges: 'Nudge References',
};

export default function DetailPanel({ nodeId, onClose, onNavigate, onboardingMap, onImpact, onClearHighlight }) {
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [width, setWidth] = useState(400);
  const [dragging, setDragging] = useState(false);
  const [zoom, setZoom] = useState(1);
  const contentRef = useRef(null);

  useEffect(() => {
    if (!nodeId) return;
    setLoading(true);
    api.nodeDetail(nodeId).then(setDetail).catch(() => setDetail(null)).finally(() => setLoading(false));
  }, [nodeId]);

  // Pinch-to-zoom: trackpad pinch fires wheel with ctrlKey=true
  useEffect(() => {
    const el = contentRef.current;
    if (!el) return;
    function handleWheel(e) {
      if (e.ctrlKey) {
        e.preventDefault();
        e.stopPropagation();
        setZoom((z) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z - e.deltaY * 0.005)));
      }
    }
    el.addEventListener('wheel', handleWheel, { passive: false });
    return () => el.removeEventListener('wheel', handleWheel);
  }, [nodeId]);

  const handleMouseDown = useCallback((e) => {
    e.preventDefault();
    setDragging(true);
    const startX = e.clientX;
    const startWidth = width;

    function onMove(ev) {
      const delta = startX - ev.clientX;
      setWidth(Math.max(300, Math.min(900, startWidth + delta)));
    }
    function onUp() {
      setDragging(false);
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    }
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [width]);

  if (!nodeId) return null;

  const primaryUrl = detail?.props?.url || detail?.props?.href || detail?.props?.link;
  const baseFontSize = 13 * zoom;

  return (
    <div
      style={{
        position: 'absolute',
        top: 0,
        right: 0,
        width,
        height: '100%',
        background: '#fff',
        borderLeft: '1px solid #e5e7eb',
        boxShadow: '-4px 0 12px rgba(0,0,0,0.08)',
        zIndex: 10,
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {/* Resize handle (drag left edge) */}
      <div
        onMouseDown={handleMouseDown}
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          width: 5,
          height: '100%',
          cursor: 'col-resize',
          background: dragging ? '#2563eb' : 'transparent',
          zIndex: 11,
        }}
        title="Drag to resize"
      />

      {/* Scrollable content */}
      <div ref={contentRef} style={{ padding: 20, flex: 1, overflow: 'auto' }}>
        {/* Header — fixed size, not zoomed */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={{ margin: 0, fontSize: 16 }}>Node Detail</h3>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {zoom !== 1 && (
              <span
                style={{ fontSize: 11, color: '#9ca3af', cursor: 'pointer' }}
                onClick={() => setZoom(1)}
                title="Reset zoom"
              >
                {Math.round(zoom * 100)}%
              </span>
            )}
            <button
              onClick={onClose}
              style={{
                background: 'none',
                border: 'none',
                fontSize: 20,
                cursor: 'pointer',
                color: '#6b7280',
              }}
            >
              x
            </button>
          </div>
        </div>

        {loading && <p style={{ color: '#9ca3af', marginTop: 16 }}>Loading...</p>}

        {detail && (
          <div style={{ marginTop: 16, fontSize: baseFontSize }}>
            <div style={{ fontSize: 11 * zoom, textTransform: 'uppercase', color: '#6b7280' }}>
              {detail.type}
            </div>
            <h2 style={{ margin: '4px 0 8px', fontSize: 18 * zoom, wordBreak: 'break-word' }}>
              {detail.props?.name || detail.id}
            </h2>

            {/* Primary link */}
            {primaryUrl && (
              <a
                href={primaryUrl}
                target="_blank"
                rel="noopener noreferrer"
                data-testid="primary-link"
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                  background: '#eff6ff',
                  color: '#2563eb',
                  padding: `${6 * zoom}px ${12 * zoom}px`,
                  borderRadius: 6,
                  fontSize: baseFontSize,
                  fontWeight: 500,
                  textDecoration: 'none',
                  marginBottom: 16,
                  border: '1px solid #bfdbfe',
                  wordBreak: 'break-all',
                }}
              >
                <span>&#8599;</span>
                Open in {primaryUrl.includes('github.com') ? 'GitHub' : primaryUrl.includes('gitlab') ? 'GitLab' : 'Browser'}
              </a>
            )}

            {/* Impact Analysis */}
            {onImpact && (
              <div style={{ display: 'flex', gap: 6, marginBottom: 12, flexWrap: 'wrap' }}>
                <button
                  onClick={() => onImpact(nodeId, 'downstream')}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 4,
                    background: '#fef2f2',
                    color: '#dc2626',
                    padding: `${5 * zoom}px ${10 * zoom}px`,
                    borderRadius: 6,
                    fontSize: 11 * zoom,
                    fontWeight: 500,
                    border: '1px solid #fecaca',
                    cursor: 'pointer',
                  }}
                >
                  Downstream Impact
                </button>
                <button
                  onClick={() => onImpact(nodeId, 'upstream')}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 4,
                    background: '#eff6ff',
                    color: '#2563eb',
                    padding: `${5 * zoom}px ${10 * zoom}px`,
                    borderRadius: 6,
                    fontSize: 11 * zoom,
                    fontWeight: 500,
                    border: '1px solid #bfdbfe',
                    cursor: 'pointer',
                  }}
                >
                  Upstream Deps
                </button>
                {onClearHighlight && (
                  <button
                    onClick={onClearHighlight}
                    style={{
                      background: '#f3f4f6',
                      color: '#6b7280',
                      padding: `${5 * zoom}px ${10 * zoom}px`,
                      borderRadius: 6,
                      fontSize: 11 * zoom,
                      border: '1px solid #e5e7eb',
                      cursor: 'pointer',
                    }}
                  >
                    Clear
                  </button>
                )}
              </div>
            )}

            {/* Description */}
            {detail.props?.description && (
              <p style={{ fontSize: baseFontSize, color: '#374151', margin: '0 0 16px', lineHeight: 1.5, wordBreak: 'break-word' }}>
                {detail.props.description}
              </p>
            )}

            {/* Properties */}
            <Section title="Properties" zoom={zoom}>
              {Object.entries(detail.props || {})
                .filter(([k]) => !k.startsWith('_') && k !== 'id' && k !== 'name' && k !== 'description')
                .map(([k, v]) => (
                  <PropRow key={k} label={k} value={String(v)} zoom={zoom} />
                ))}
            </Section>

            {/* Gaps */}
            {detail.gaps?.length > 0 && (
              <Section title={`Issues (${detail.gaps.length})`} zoom={zoom}>
                {detail.gaps.map((g, i) => (
                  <div
                    key={i}
                    style={{
                      padding: `${6 * zoom}px ${8 * zoom}px`,
                      marginBottom: 4,
                      borderRadius: 4,
                      background: '#fef3c7',
                      borderLeft: `3px solid ${SEVERITY_COLORS[g.severity] || '#f59e0b'}`,
                      fontSize: 12 * zoom,
                      wordBreak: 'break-word',
                    }}
                  >
                    <strong>{g.type}</strong>: {g.message}
                  </div>
                ))}
              </Section>
            )}

            {/* Onboarding */}
            {onboardingMap?.get(detail.id || nodeId) && (
              <OnboardingSection
                data={onboardingMap.get(detail.id || nodeId)}
                zoom={zoom}
              />
            )}

            {/* Neighbors */}
            {detail.neighbors?.length > 0 && (
              <Section title={`Connections (${detail.neighbors.length})`} zoom={zoom}>
                {detail.neighbors.map((n, i) => (
                  <div
                    key={i}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 8,
                      padding: `${4 * zoom}px 0`,
                      fontSize: 12 * zoom,
                      cursor: 'pointer',
                      flexWrap: 'wrap',
                    }}
                    onClick={() => onNavigate(n.id)}
                  >
                    <span style={{ color: '#9ca3af' }}>
                      {n.direction === 'outgoing' ? '→' : '←'}
                    </span>
                    <span style={{ color: '#6b7280', minWidth: 70 }}>{n.relationship}</span>
                    <span style={{ fontWeight: 500 }}>{n.name || n.id}</span>
                    <span style={{ color: '#9ca3af', fontSize: 10 * zoom }}>{n.type}</span>
                  </div>
                ))}
              </Section>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function Section({ title, children, zoom = 1 }) {
  const [open, setOpen] = useState(true);
  return (
    <div style={{ marginBottom: 16 }}>
      <h4
        style={{
          fontSize: 12 * zoom,
          color: '#374151',
          marginBottom: 6,
          textTransform: 'uppercase',
          cursor: 'pointer',
          userSelect: 'none',
          display: 'flex',
          alignItems: 'center',
          gap: 4,
        }}
        onClick={() => setOpen(!open)}
      >
        <span style={{ fontSize: 10 * zoom, transition: 'transform 0.2s', transform: open ? 'rotate(90deg)' : 'rotate(0deg)' }}>
          &#9654;
        </span>
        {title}
      </h4>
      {open && children}
    </div>
  );
}

function OnboardingSection({ data, zoom = 1 }) {
  if (!data) return null;
  const checks = data.checks || {};
  const checkOrder = ['repository', 'branch', 'container_image', 'pac', 'builds', 'last_built', 'nudges'];

  return (
    <Section title={`Onboarding (${data.score}%)`} zoom={zoom}>
      {/* Score bar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <div
          style={{
            flex: 1,
            height: 6,
            background: '#e5e7eb',
            borderRadius: 3,
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              width: `${data.score}%`,
              height: '100%',
              background: data.badge_color,
              borderRadius: 3,
              transition: 'width 0.3s ease',
            }}
          />
        </div>
        <span style={{ fontSize: 11 * zoom, fontWeight: 600, color: data.badge_color }}>
          {data.overall}
        </span>
      </div>

      {/* Check steps */}
      {checkOrder.map((key) => {
        const check = checks[key];
        if (!check) return null;
        const statusInfo = CHECK_STATUS_ICONS[check.status] || CHECK_STATUS_ICONS.SKIP;
        return (
          <div
            key={key}
            style={{
              display: 'flex',
              alignItems: 'flex-start',
              gap: 8,
              padding: `${4 * zoom}px 0`,
              fontSize: 12 * zoom,
              borderBottom: '1px solid #f3f4f6',
            }}
          >
            <span
              style={{
                color: statusInfo.color,
                fontWeight: 700,
                fontSize: 13 * zoom,
                minWidth: 16,
                textAlign: 'center',
              }}
            >
              {statusInfo.icon}
            </span>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 500 }}>{CHECK_LABELS[key] || key}</div>
              <div style={{ color: '#6b7280', fontSize: 11 * zoom }}>{check.detail}</div>
              {check.fix && (
                <div style={{ color: '#f59e0b', fontSize: 11 * zoom, marginTop: 2 }}>
                  Fix: {check.fix}
                </div>
              )}
            </div>
          </div>
        );
      })}

      {/* Jira link */}
      {data.jira_key && (
        <div style={{ marginTop: 8, fontSize: 12 * zoom }}>
          <a
            href={`https://issues.redhat.com/browse/${data.jira_key}`}
            target="_blank"
            rel="noopener noreferrer"
            style={{ color: '#2563eb', textDecoration: 'none' }}
          >
            {data.jira_key} &#8599;
          </a>
        </div>
      )}
    </Section>
  );
}

function PropRow({ label, value, zoom = 1 }) {
  const linkable = URL_PROPS.has(label) || isUrl(value);

  return (
    <div style={{ display: 'flex', fontSize: 12 * zoom, padding: `${3 * zoom}px 0`, alignItems: 'flex-start' }}>
      <span style={{ color: '#6b7280', minWidth: 110, flexShrink: 0 }}>{formatPropKey(label)}</span>
      {linkable ? (
        <a
          href={value}
          target="_blank"
          rel="noopener noreferrer"
          style={{
            color: '#2563eb',
            textDecoration: 'none',
            wordBreak: 'break-all',
          }}
          title={value}
        >
          {value}
        </a>
      ) : (
        <span
          style={{
            wordBreak: 'break-word',
          }}
          title={value}
        >
          {value}
        </span>
      )}
    </div>
  );
}
