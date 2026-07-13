import { useEffect, useState, useRef } from 'react';
import { api } from '../api';

/**
 * Polls /api/map/live-status every `interval` ms.
 * Returns a Map<nodeId, NodeStatus> and activity events.
 * Keeps stale data on error (graceful degradation).
 */
export function useLiveStatus(application = 'rhoai-v3-5', interval = 60000) {
  const [statusMap, setStatusMap] = useState(new Map());
  const [onboardingMap, setOnboardingMap] = useState(new Map());
  const [activity, setActivity] = useState([]);
  const [icAvailable, setIcAvailable] = useState(false);
  const [lastUpdated, setLastUpdated] = useState(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;

    async function fetchStatus() {
      try {
        const data = await api.liveStatus(application);
        if (!mountedRef.current) return;

        const map = new Map();
        for (const node of (data.nodes || [])) {
          map.set(node.node_id, node);
        }
        setStatusMap(map);

        const obMap = new Map();
        for (const ob of (data.onboarding || [])) {
          obMap.set(ob.node_id, ob);
        }
        setOnboardingMap(obMap);

        setActivity(data.activity || []);
        setIcAvailable(data.ic_available !== false);
        setLastUpdated(data.last_updated || null);
      } catch {
        if (!mountedRef.current) return;
        setIcAvailable(false);
      }
    }

    fetchStatus();
    const timer = setInterval(fetchStatus, interval);

    return () => {
      mountedRef.current = false;
      clearInterval(timer);
    };
  }, [application, interval]);

  return { statusMap, onboardingMap, activity, icAvailable, lastUpdated };
}
