/**
 * Format a UTC timestamp string as IST (Indian Standard Time, UTC+5:30).
 *
 * The backend stores all timestamps as UTC naive strings (e.g. "2026-07-09T08:12:00").
 * The Pi's OS clock is in EDT, so without explicit timezone handling the browser
 * would show EDT times. This utility always interprets stored strings as UTC and
 * displays them in Asia/Kolkata (IST).
 *
 * @param {string|null} timestamp - ISO-like datetime string from the API
 * @param {object} opts - Optional extra Intl.DateTimeFormat options
 * @returns {string} Time formatted in IST, e.g. "1:42:05 PM"
 */
export function formatTimeIST(timestamp, opts = {}) {
  if (!timestamp) return '--:--';
  try {
    // If the string has no timezone indicator, treat it as UTC by appending 'Z'.
    // Strings that already have '+', '-' offset (after position 10), or end with 'Z'
    // are left untouched.
    const hasOffset =
      timestamp.endsWith('Z') ||
      /[+-]\d{2}:\d{2}$/.test(timestamp);
    const utcStr = hasOffset ? timestamp : timestamp.replace(' ', 'T') + 'Z';

    return new Date(utcStr).toLocaleTimeString('en-IN', {
      timeZone: 'Asia/Kolkata',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: true,
      ...opts,
    });
  } catch {
    return timestamp;
  }
}

/**
 * Format a UTC timestamp string as a full date-time in IST.
 * e.g. "09/07/2026, 1:42:05 PM"
 */
export function formatDateTimeIST(timestamp) {
  if (!timestamp) return '--';
  try {
    const hasOffset =
      timestamp.endsWith('Z') ||
      /[+-]\d{2}:\d{2}$/.test(timestamp);
    const utcStr = hasOffset ? timestamp : timestamp.replace(' ', 'T') + 'Z';

    return new Date(utcStr).toLocaleString('en-IN', {
      timeZone: 'Asia/Kolkata',
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: true,
    });
  } catch {
    return timestamp;
  }
}
