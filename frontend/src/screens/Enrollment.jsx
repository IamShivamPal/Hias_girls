import { useState, useEffect } from 'react';
import { UserCheck, ShieldAlert, Scan, RefreshCcw, Search, UserPlus, Clock, Users } from 'lucide-react';
import { API_ENDPOINTS } from '../api/config';

export default function Enrollment() {
  const [users, setUsers] = useState([]);
  const [devices, setDevices] = useState([]);
  const [selectedGateIdx, setSelectedGateIdx] = useState(0);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState({ userId: null, message: null, type: 'info' });

  useEffect(() => {
    fetchUsers();
    fetchDevices();
  }, []);

  const fetchUsers = async () => {
    try {
      const res = await fetch(API_ENDPOINTS.USERS);
      const data = await res.json();
      setUsers(data);
    } catch (err) {
      console.error("Failed to fetch users", err);
    } finally {
      setLoading(false);
    }
  };

  const fetchDevices = async () => {
    try {
      const res = await fetch(API_ENDPOINTS.SYSTEM_DEVICES);
      const data = await res.json();
      // Filter for only hardware devices (the actual gates)
      setDevices(data.filter(dev => dev.type === 'hardware'));
    } catch (err) {
      console.error("Failed to fetch devices", err);
    }
  };

  const handleEnroll = async (userId) => {
    const deviceName = devices[selectedGateIdx]?.name || `Gate ${selectedGateIdx + 1}`;
    setStatus({ userId, message: `Initiating face scan on ${deviceName}...`, type: 'info' });
    try {
      const res = await fetch(API_ENDPOINTS.USERS_ENROLL_FACE(userId, selectedGateIdx), { method: 'POST' });
      if (res.ok) {
        setStatus({ userId, message: `Scanning on ${deviceName}. Please look at the camera.`, type: 'success' });
        // Clear message after 15 seconds
        setTimeout(() => setStatus({ userId: null, message: null, type: 'info' }), 15000);
      } else {
        const errorData = await res.json();
        throw new Error(errorData.detail || "Failed to trigger enrollment");
      }
    } catch (err) {
      setStatus({ userId, message: `Failed: ${err.message}`, type: 'error' });
      setTimeout(() => setStatus({ userId: null, message: null, type: 'info' }), 5000);
    }
  };

  const handleSyncAll = async () => {
    setStatus({ userId: 'ALL', message: 'Syncing all user profiles to all gates...', type: 'info' });
    try {
      const res = await fetch(API_ENDPOINTS.USERS_SYNC_ALL, { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        setStatus({ userId: 'ALL', message: `Success! Profiles pushed to all gates. Ready for enrollment.`, type: 'success' });
        setTimeout(() => setStatus({ userId: null, message: null, type: 'info' }), 5000);
      } else {
        throw new Error("Failed to sync devices");
      }
    } catch (err) {
      setStatus({ userId: 'ALL', message: `Sync Failed: ${err.message}`, type: 'error' });
      setTimeout(() => setStatus({ userId: null, message: null, type: 'info' }), 5000);
    }
  };

  const handleComplete = async (userId) => {
    try {
      const res = await fetch(API_ENDPOINTS.USERS_ENROLL_COMPLETE(userId), { method: 'POST' });
      if (res.ok) {
        setStatus({ userId, message: 'User marked as fully enrolled.', type: 'success' });
        fetchUsers(); // Refresh list to remove user
        setTimeout(() => setStatus({ userId: null, message: null, type: 'info' }), 3000);
      }
    } catch (err) {
      console.error("Failed to complete enrollment", err);
    }
  };

  const filteredUsers = users.filter(u =>
    u.name.toLowerCase().includes(search.toLowerCase()) ||
    u.user_id.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div style={{ padding: '20px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '30px' }}>
        <div>
          <h1 style={{ fontSize: '24px', display: 'flex', alignItems: 'center', gap: '12px' }}>
            <Scan color="#ff3b8f" /> FACE ENROLLMENT
          </h1>
          <p style={{ color: '#94a3b8', fontSize: '14px' }}>Register new faces to the biometric hardware</p>
        </div>

        <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
             <label style={{ fontSize: '10px', color: '#64748b', fontWeight: 'bold' }}>TARGET DEVICE</label>
             <select 
               value={selectedGateIdx}
               onChange={(e) => setSelectedGateIdx(parseInt(e.target.value))}
               style={{
                 backgroundColor: '#0a0c14',
                 color: 'white',
                 border: '1px solid #1e293b',
                 padding: '8px 12px',
                 borderRadius: '8px',
                 fontSize: '12px',
                 outline: 'none',
                 cursor: 'pointer'
               }}
             >
               {devices.map((dev, idx) => (
                 <option key={idx} value={idx}>
                   {dev.name} {dev.url ? `(${dev.url.split('://')[1] || dev.url})` : ''}
                 </option>
               ))}
               {devices.length === 0 && <option value={0}>Loading devices...</option>}
             </select>
          </div>

          <div style={{ display: 'flex', gap: '8px', marginTop: '18px' }}>
            <button 
              onClick={handleSyncAll}
              style={{ 
                display: 'flex', 
                alignItems: 'center', 
                gap: '8px', 
                backgroundColor: 'rgba(168, 85, 247, 0.1)',
                color: '#a855f7',
                border: '1px solid rgba(168, 85, 247, 0.2)',
                padding: '8px 16px',
                borderRadius: '8px',
                cursor: 'pointer',
                fontSize: '12px',
                fontWeight: '600',
              }}
            >
              <Users size={16} /> PUSH PROFILES (SYNC)
            </button>

            <button 
              onClick={async () => {
                setStatus({ userId: 'SYNC_DEV', message: 'Fetching users from Master Gate...', type: 'info' });
                try {
                  const res = await fetch(API_ENDPOINTS.USERS_SYNC, { method: 'POST' });
                  if (res.ok) {
                    await fetchUsers();
                    setStatus({ userId: 'SYNC_DEV', message: 'Sync complete! Users imported.', type: 'success' });
                    setTimeout(() => setStatus({ userId: null, message: null }), 3000);
                  }
                } catch (err) {
                  setStatus({ userId: 'SYNC_DEV', message: 'Sync failed', type: 'error' });
                }
              }}
              style={{ 
                display: 'flex', 
                alignItems: 'center', 
                gap: '8px', 
                backgroundColor: 'rgba(34, 197, 94, 0.1)',
                color: '#22c55e',
                border: '1px solid rgba(34, 197, 94, 0.2)',
                padding: '8px 16px',
                borderRadius: '8px',
                cursor: 'pointer',
                fontSize: '12px',
                fontWeight: '600',
              }}
            >
              <RefreshCcw size={16} /> SYNC FROM MASTER
            </button>
          </div>
          
          <div style={{ position: 'relative', marginTop: '18px' }}>
            <Search size={18} color="#64748b" style={{ position: 'absolute', left: '12px', top: '50%', transform: 'translateY(-50%)' }} />
            <input
              type="text"
              placeholder="Search users..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{ backgroundColor: '#0a0c14', border: '1px solid #1e293b', padding: '10px 12px 10px 40px', borderRadius: '8px', color: 'white', width: '250px' }}
            />
          </div>
        </div>
      </div>

      {status.message && (
        <div style={{
          backgroundColor: status.type === 'error' ? 'rgba(239, 68, 68, 0.1)' : 'rgba(34, 197, 94, 0.1)',
          border: `1px solid ${status.type === 'error' ? '#ef4444' : '#22c55e'}`,
          padding: '16px',
          borderRadius: '12px',
          marginBottom: '20px',
          display: 'flex',
          alignItems: 'center',
          gap: '12px',
          boxShadow: `0 0 20px ${status.type === 'error' ? 'rgba(239, 68, 68, 0.2)' : 'rgba(34, 197, 94, 0.2)'}`,
          animation: status.type !== 'error' ? 'pulse 2s infinite' : 'none'
        }}>
          <div style={{
            width: '40px',
            height: '40px',
            borderRadius: '10px',
            backgroundColor: status.type === 'error' ? 'rgba(239, 68, 68, 0.2)' : 'rgba(34, 197, 94, 0.2)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center'
          }}>
            {status.type === 'error' ? <ShieldAlert color="#ef4444" /> : <RefreshCcw className="spin" color="#22c55e" />}
          </div>
          <div style={{ flex: 1 }}>
            <p style={{ fontWeight: 'bold', color: status.type === 'error' ? '#ef4444' : '#22c55e', fontSize: '15px' }}>{status.message}</p>
            <p style={{ fontSize: '12px', color: '#94a3b8' }}>UserID: {status.userId} • Action: Facial Capture</p>
          </div>
          {status.type === 'success' && (
            <div style={{ fontSize: '12px', color: '#22c55e', display: 'flex', alignItems: 'center', gap: '6px' }}>
              <Clock size={14} /> ACTIVE
            </div>
          )}
        </div>
      )}

      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ textAlign: 'left', borderBottom: '1px solid #1e293b', color: '#64748b', fontSize: '12px' }}>
              <th style={{ padding: '16px' }}>USER PROFILE</th>
              <th style={{ padding: '16px' }}>ROLE</th>
              <th style={{ padding: '16px' }}>STATUS</th>
              <th style={{ padding: '16px' }}>LAST SYNC</th>
              <th style={{ padding: '16px', textAlign: 'right' }}>COMMAND</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan="4" style={{ padding: '60px', textAlign: 'center' }}>
                <RefreshCcw className="spin" color="#ff3b8f" size={32} style={{ marginBottom: '12px' }} />
                <div style={{ color: '#64748b' }}>Loading database users...</div>
              </td></tr>
            ) : filteredUsers.length === 0 ? (
              <tr><td colSpan="4" style={{ padding: '60px', textAlign: 'center' }}>
                <Search size={32} color="#1e293b" style={{ marginBottom: '12px' }} />
                <div style={{ color: '#64748b' }}>No users found matching your search.</div>
              </td></tr>
            ) : filteredUsers.map((user) => (
              <tr key={user.user_id} style={{ borderBottom: '1px solid #0f172a' }} className="row-hover">
                <td style={{ padding: '16px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                    <div style={{ width: '36px', height: '36px', borderRadius: '50%', backgroundColor: '#1e293b', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#64748b', fontWeight: 'bold' }}>
                      {user.name.charAt(0)}
                    </div>
                    <div>
                      <div style={{ fontWeight: 'bold' }}>{user.name}</div>
                      <div style={{ fontSize: '12px', color: '#64748b' }}>{user.user_id}</div>
                    </div>
                  </div>
                </td>
                <td style={{ padding: '16px' }}>
                  <span style={{ 
                    fontSize: '11px', 
                    padding: '4px 8px', 
                    backgroundColor: user.face_enrolled ? 'rgba(34, 197, 94, 0.1)' : 'rgba(245, 158, 11, 0.1)', 
                    borderRadius: '4px', 
                    color: user.face_enrolled ? '#22c55e' : '#f59e0b',
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '4px'
                  }}>
                    {user.face_enrolled ? <UserCheck size={12} /> : <Clock size={12} />}
                    {user.face_enrolled ? 'ENROLLED' : 'PENDING'}
                  </span>
                </td>
                <td style={{ padding: '16px', color: '#475569', fontSize: '12px' }}>
                  Synced Recently
                </td>
                <td style={{ padding: '16px', textAlign: 'right' }}>
                  <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                    <button
                      onClick={() => handleEnroll(user.user_id)}
                      disabled={status.userId === user.user_id}
                      style={{
                        backgroundColor: status.userId === user.user_id ? '#1e293b' : (user.face_enrolled ? 'rgba(148, 163, 184, 0.1)' : '#ff3b8f'),
                        color: user.face_enrolled ? '#94a3b8' : 'white',
                        border: user.face_enrolled ? '1px solid #1e293b' : 'none',
                        padding: '10px 20px',
                        borderRadius: '8px',
                        cursor: status.userId === user.user_id ? 'not-allowed' : 'pointer',
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: '8px',
                        fontWeight: '600',
                        transition: 'all 0.2s',
                        boxShadow: (status.userId === user.user_id || user.face_enrolled) ? 'none' : '0 4px 12px rgba(255, 59, 143, 0.3)'
                      }}>
                      {status.userId === user.user_id ? <RefreshCcw size={16} className="spin" /> : <Scan size={16} />}
                      {status.userId === user.user_id ? 'WAITING...' : (user.face_enrolled ? `RE-ENROLL ON GATE ${selectedGateIdx + 1}` : `ENROLL ON GATE ${selectedGateIdx + 1}`)}
                    </button>
                    
                    {!user.face_enrolled && (
                      <button
                        onClick={() => handleComplete(user.user_id)}
                        title="Mark as fully enrolled on all gates"
                        style={{
                          backgroundColor: 'rgba(34, 197, 94, 0.1)',
                          color: '#22c55e',
                          border: '1px solid rgba(34, 197, 94, 0.2)',
                          padding: '10px',
                          borderRadius: '8px',
                          cursor: 'pointer',
                          display: 'inline-flex',
                          alignItems: 'center',
                          transition: 'all 0.2s'
                        }}>
                        <UserCheck size={20} />
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
