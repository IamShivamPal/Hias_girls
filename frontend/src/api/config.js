// Centralized API Configuration
// Vite uses 'import.meta.env' for environment variables.
// On Render, we will set VITE_API_URL to the backend URL.

// Dynamically detect the backend IP based on how the site is accessed
const getBaseUrl = () => {
    if (import.meta.env.VITE_API_URL) return import.meta.env.VITE_API_URL;
    const hostname = window.location.hostname;
    return `http://${hostname}:8000`;
};

export const API_BASE_URL = getBaseUrl();

export const API_ENDPOINTS = {
    ACCESS_EVENT: `${API_BASE_URL}/access/event`,
    EVENTS: `${API_BASE_URL}/events`,
    STREAM: `${API_BASE_URL}/events/stream`,
    SETTINGS: `${API_BASE_URL}/settings`,
    SYSTEM_DEVICES: `${API_BASE_URL}/system/devices`,
    REVIEW_QUEUE: `${API_BASE_URL}/review/queue`,
    REVIEW_ACTION: `${API_BASE_URL}/review/action`,
    REPORTS_STATS: `${API_BASE_URL}/reports/stats`,
    REPORTS_EXPORT: `${API_BASE_URL}/reports/export`,
    ALERTS: `${API_BASE_URL}/alerts`,
    USERS: `${API_BASE_URL}/users`,
    USERS_SEARCH: `${API_BASE_URL}/users/search`,
    USERS_SYNC: `${API_BASE_URL}/users/sync-from-device`,
    USERS_SYNC_ALL: `${API_BASE_URL}/users/sync-all`,
    USERS_ENROLL_FACE: (id, gateIdx = 0) => `${API_BASE_URL}/users/${id}/enroll-face?gate_idx=${gateIdx}`,
    USERS_ENROLL_COMPLETE: (id) => `${API_BASE_URL}/users/${id}/enroll-complete`,
    STATS_SUMMARY: `${API_BASE_URL}/stats/summary`,
    SIMULATE_REVIEW: `${API_BASE_URL}/simulate/review`,
};
