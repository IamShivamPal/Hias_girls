# Hardware Abstraction Layer — Real Relay Integration
import time
import threading
from app.logger import log_relay

try:
    from gpiozero import LED
    GPIO_ENABLED = True
except (ImportError, RuntimeError):
    GPIO_ENABLED = False

# Configuration: Unique GPIO pins for each gate
GATE_PIN_MAP = {
    "Entry 1": 17,
    "Entry 2": 18,
    "Exit 1": 27,
    "Exit 2": 22
}
TRIGGER_DURATION = 1.5  # Seconds

# Initialize all LEDs
leds = {}
if GPIO_ENABLED:
    for name, pin in GATE_PIN_MAP.items():
        try:
            # Active Low logic as requested earlier
            leds[name] = LED(pin, active_high=False)
        except Exception as e:
            print(f"[HARDWARE_ERROR] Could not initialize GPIO {pin} for {name}: {e}")


def _trigger_logic(device_name: str):
    """Logic to pulse the relay/LED for a specific device."""
    pin = GATE_PIN_MAP.get(device_name, "Unknown")
    
    if GPIO_ENABLED and device_name in leds:
        try:
            print(f"[GPIO_PULSE] {device_name} -> Pin {pin} (ON)")
            leds[device_name].on()
            time.sleep(TRIGGER_DURATION)
            leds[device_name].off()
            print(f"[GPIO_PULSE] {device_name} -> Pin {pin} (OFF)")
        except Exception as e:
            print(f"[HARDWARE_ERROR] Relay failure on {device_name} (Pin {pin}): {e}")
    else:
        # SIMULATION MODE or local fallback
        print(f"[SIMULATION] {device_name} -> Pin {pin} (PULSE {TRIGGER_DURATION}s)")
        time.sleep(TRIGGER_DURATION)


def open_gate(trace_id: str, user_id: str, device_id: str = "local") -> None:
    """
    Trigger gate relay to open.
    Can trigger local GPIO and/or remote COSEC device.
    """
    print(f"[RELAY_TRIGGERED] trace_id={trace_id} | user_id={user_id} | device={device_id}")
    log_relay(trace_id, user_id)

    # 1. Always trigger local GPIO (LED/Simulation) for visual feedback
    threading.Thread(target=_trigger_logic, args=(device_id,), daemon=True).start()

    # 2. Check if the device is one of our hardware gates and trigger it too
    from app.background import COSEC_CONFIG
    from app.adapters.cosec import COSECAdapter
    import asyncio

    device_info = next((d for d in COSEC_CONFIG["devices"] if d["name"] == device_id or d["id"] == device_id), None)

    if device_info:
        # Trigger remote COSEC door
        adapter = COSECAdapter(device_info["url"], COSEC_CONFIG["username"], COSEC_CONFIG["password"])
        
        def _remote_trigger():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(adapter.open_door())
                loop.close()
            except Exception as e:
                print(f"[HARDWARE_ERROR] Remote unlock failed for {device_id}: {e}")
        
        threading.Thread(target=_remote_trigger, daemon=True).start()


def close_gate(trace_id: str, user_id: str) -> None:
    """Trigger gate relay to close (placeholder for explicit logic)."""
    print(f"[RELAY_CLOSED] trace_id={trace_id} | user_id={user_id}")

