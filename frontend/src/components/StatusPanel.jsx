import React, { useState, useEffect } from 'react';
import { CheckCircle2, XCircle, Wifi, Database, Activity, ShieldCheck, Zap } from 'lucide-react';
import { API_ENDPOINTS } from '../api/config';

export default function StatusPanel({ connected }) {
  const [data, setData] = useState({
    controller: connected ? 'Online' : 'Offline',
    face_devices: 'Checking...',
    rfid_readers: 'Healthy',
    relays: 'Online',
    network: 'Online',
    power: 'Normal'
  });

  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const res = await fetch(API_ENDPOINTS.SYSTEM_STATUS);
        const json = await res.json();
        setData(json);
      } catch (err) {
        setData(prev => ({ ...prev, controller: 'Offline' }));
      }
    };

    fetchStatus();
    const interval = setInterval(fetchStatus, 5000);
    return () => clearInterval(interval);
  }, [connected]);

  const statuses = [
    { name: 'Face Devices', status: data.face_devices, icon: ShieldCheck },
    { name: 'RFID Readers', status: data.rfid_readers, icon: Database },
    { name: 'Network', status: data.network, icon: Wifi },
    { name: 'Power Supply', status: data.power, icon: Zap },
  ];

  return (
    <div className="card">
      <h3 style={{ marginBottom: '16px', fontSize: '14px', display: 'flex', alignItems: 'center', gap: '8px' }}>
        <Activity size={18} color="#ff3b8f" /> SYSTEM STATUS
      </h3>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
        {statuses.map((s, i) => (
          <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '13px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#94a3b8' }}>
              <s.icon size={16} />
              <span>{s.name}</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <span style={{ color: (s.status === 'Offline' || s.status === 'Critical') ? '#ef4444' : '#fff' }}>{s.status}</span>
              <div className={`status-indicator ${(s.status === 'Offline' || s.status === 'Critical') ? 'offline' : 'online'}`} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
