from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import serial
import json
import os
import time
import threading

app = Flask(__name__)
app.config['SECRET_KEY'] = 'kyun-ai-secret-key-2024'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

DB_FILE = 'database.json'
ANALYTICS_FILE = 'analytics.json'

# --- HARDWARE CORE CONFIGURATION ---
hardware_state = {
    "led_status": "off",
    "last_command_time": time.time(),
    "handshake_complete": False,
    "stepper_angle": 0.0,
    "current_angles": [90, 90, 90, 90, 90, 90]
}

# --- EDITOR/VIEWER STATE ---
editor_state = {
    "current_editor_id": None,
    "editor_sid": None,
    "viewer_count": 0
}

# Serial lock for thread safety
serial_lock = threading.Lock()

try:
    ser = serial.Serial('COM11', 115200, timeout=0.5)
    time.sleep(2)
    print("KYUN AI SYSTEM: Hardware Link Established Successfully.")

    # Startup handshake flash
    with serial_lock:
        ser.write(bytearray([254]))  # LED ON
        time.sleep(0.3)
        ser.write(bytearray([253]))  # LED OFF
    hardware_state["handshake_complete"] = True
except Exception as e:
    ser = None
    print(f"KYUN AI SYSTEM: Hardware Offline. Reason: {e}")

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r') as f:
            try:
                data = json.load(f)
                if isinstance(data, list):
                    return {"slots": data}
                return data
            except:
                pass
    return {"slots": []}

def save_db(data):
    with open(DB_FILE, 'w') as f:
        json.dump(data, f, indent=4)

def load_analytics():
    if os.path.exists(ANALYTICS_FILE):
        with open(ANALYTICS_FILE, 'r') as f:
            try:
                return json.load(f)
            except:
                pass
    return {"total_moves": 0, "sequence_runs": 0, "last_action": ""}

def save_analytics(data):
    with open(ANALYTICS_FILE, 'w') as f:
        json.dump(data, f, indent=4)

# --- WEBSOCKET EVENT HANDLERS ---
@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}")
    # Send current state to newly connected client
    emit('sync_state', {
        'angles': hardware_state.get('current_angles', [90, 90, 90, 90, 90, 90]),
        'stepper_angle': hardware_state.get('stepper_angle', 0),
        'led_status': hardware_state.get('led_status', 'off'),
        'current_editor_id': editor_state.get('current_editor_id'),
        'editor_sid': editor_state.get('editor_sid')
    }, room=request.sid)

@socketio.on('disconnect')
def handle_disconnect():
    print(f"Client disconnected: {request.sid}")
    # If the disconnected client was the editor, release editor role
    if editor_state.get('editor_sid') == request.sid:
        editor_state['current_editor_id'] = None
        editor_state['editor_sid'] = None
        emit('editor_released', {'message': 'Editor has disconnected, role released'}, broadcast=True)

@socketio.on('request_editor_role')
def handle_request_editor_role(data):
    """Request to become editor"""
    client_name = data.get('client_name', 'Unknown')
    
    # Check if editor already exists
    if editor_state.get('current_editor_id') is not None:
        emit('editor_role_denied', {
            'message': f'Editor role already taken by {editor_state["current_editor_id"]}',
            'current_editor': editor_state['current_editor_id']
        }, room=request.sid)
    else:
        # Grant editor role
        editor_state['current_editor_id'] = client_name
        editor_state['editor_sid'] = request.sid
        emit('editor_role_granted', {
            'message': 'You are now the editor. Others will view only.',
            'client_name': client_name
        }, room=request.sid)
        emit('editor_role_taken', {
            'message': f'{client_name} is now the editor',
            'editor_name': client_name
        }, broadcast=True, include_self=False)

@socketio.on('release_editor_role')
def handle_release_editor_role():
    """Release editor role"""
    if editor_state.get('editor_sid') == request.sid:
        editor_state['current_editor_id'] = None
        editor_state['editor_sid'] = None
        emit('editor_role_released', {'message': 'Editor role released'}, room=request.sid)
        emit('editor_released', {'message': 'Editor role is now available'}, broadcast=True, include_self=False)

@socketio.on('move_sync')
def handle_move_sync(data):
    """Broadcast motor movement to all connected clients (only if sender is editor)"""
    # Check if sender is editor
    if editor_state.get('editor_sid') != request.sid:
        emit('action_denied', {'message': 'Only editor can control the arm'}, room=request.sid)
        return
    
    angles = data.get('angles')
    hardware_control = data.get('hardware_control', True)  # Default to control hardware
    
    if angles and len(angles) == 6:
        # Store current angles in hardware_state
        hardware_state['current_angles'] = angles
        
        # Broadcast to all other clients (viewers)
        emit('angle_update', {
            'angles': angles,
            'source': request.sid,
            'editor_name': editor_state.get('current_editor_id')
        }, broadcast=True, include_self=False)
        
        # Also send to serial if hardware control is enabled and not in simulation
        if ser and hardware_control and not data.get('simulation', False):
            with serial_lock:
                try:
                    ser.reset_input_buffer()
                    ser.reset_output_buffer()
                    packet = bytearray([0xFF] + [max(0, min(180, int(round(float(a))))) for a in angles])
                    ser.write(packet)
                    ser.flush()
                    print(f"Hardware updated: {angles}")
                except Exception as e:
                    print(f"Serial error: {e}")

@socketio.on('stepper_sync')
def handle_stepper_sync(data):
    """Broadcast stepper movement to all connected clients (only if sender is editor)"""
    if editor_state.get('editor_sid') != request.sid:
        emit('action_denied', {'message': 'Only editor can control the stepper'}, room=request.sid)
        return
    
    angle = data.get('angle')
    if angle is not None:
        hardware_state['stepper_angle'] = angle
        emit('stepper_update', {
            'angle': angle,
            'source': request.sid,
            'editor_name': editor_state.get('current_editor_id')
        }, broadcast=True, include_self=False)

@socketio.on('led_sync')
def handle_led_sync(data):
    """Broadcast LED state to all connected clients (only if sender is editor)"""
    if editor_state.get('editor_sid') != request.sid:
        emit('action_denied', {'message': 'Only editor can control the LED'}, room=request.sid)
        return
    
    mode = data.get('mode')
    if mode:
        hardware_state['led_status'] = mode
        emit('led_update', {
            'mode': mode,
            'source': request.sid,
            'editor_name': editor_state.get('current_editor_id')
        }, broadcast=True, include_self=False)
        
        # Send to serial if hardware available
        if ser:
            cmd_map = {'on': 254, 'off': 253, 'blink': 252}
            cmd = cmd_map.get(mode)
            if cmd:
                with serial_lock:
                    ser.write(bytearray([cmd]))
                    ser.flush()

@socketio.on('request_full_state')
def handle_request_full_state():
    """Send complete current state to a client"""
    emit('full_state', {
        'angles': hardware_state.get('current_angles', [90, 90, 90, 90, 90, 90]),
        'stepper_angle': hardware_state.get('stepper_angle', 0),
        'led_status': hardware_state.get('led_status', 'off'),
        'slots': load_db().get('slots', []),
        'analytics': load_analytics(),
        'current_editor_id': editor_state.get('current_editor_id'),
        'is_editor': editor_state.get('editor_sid') == request.sid
    }, room=request.sid)

@socketio.on('slots_sync')
def handle_slots_sync(data):
    """Broadcast slots update to all connected clients (only if sender is editor)"""
    if editor_state.get('editor_sid') != request.sid:
        return
    
    slots = data.get('slots')
    if slots:
        emit('slots_update', {
            'slots': slots,
            'source': request.sid
        }, broadcast=True, include_self=False)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_slots')
def get_slots():
    return jsonify(load_db())

@app.route('/save_slots', methods=['POST'])
def save_slots():
    data = request.json
    save_db(data)
    # Broadcast slots update to all connected clients
    socketio.emit('slots_update', {'slots': data.get('slots', [])}, broadcast=True)
    return jsonify(status="Synced", message="Core Memory Updated")

@app.route('/move', methods=['POST'])
def move():
    """
    Servo control — uses header 0xFF followed by 6 angle bytes.
    FIXED: Added better error handling and serial write confirmation
    """
    angles = request.json.get('angles')
    if not angles or len(angles) != 6:
        return jsonify(success=False, error="Invalid angles array"), 400
    
    clamped = [max(0, min(180, int(round(float(a))))) for a in angles]
    
    # Store in hardware state
    hardware_state['current_angles'] = clamped
    
    # Broadcast to all WebSocket clients
    socketio.emit('angle_update', {'angles': clamped, 'source': 'api'}, broadcast=True)
    
    if ser:
        with serial_lock:
            try:
                # Clear buffers before sending
                ser.reset_input_buffer()
                ser.reset_output_buffer()
                
                # Send packet
                packet = bytearray([0xFF] + clamped)
                ser.write(packet)
                ser.flush()
                
                # Wait for acknowledgment
                time.sleep(0.05)
                
                # Read response if available (for debugging)
                if ser.in_waiting:
                    response = ser.readline().decode('utf-8', errors='ignore').strip()
                    if response.startswith("OK:"):
                        print(f"Arduino confirmed: {response}")
                
                # Update analytics
                analytics = load_analytics()
                analytics["total_moves"] = analytics.get("total_moves", 0) + 1
                analytics["last_action"] = time.strftime("%H:%M:%S")
                save_analytics(analytics)
                
                return jsonify(success=True, angles=clamped)
            except Exception as e:
                print(f"Serial write error: {e}")
                return jsonify(success=False, error=str(e)), 500
    return jsonify(success=False, error="Serial connection inactive")

@app.route('/stepper', methods=['POST'])
def stepper_control():
    """
    Stepper motor control — absolute angle 0-360 degrees.
    Packet format: [0xFC] [angle_high] [angle_low]
    """
    angle = request.json.get('angle')
    if angle is None:
        return jsonify(success=False, error="No angle provided"), 400

    angle = max(0.0, min(360.0, float(angle)))
    angle10 = int(round(angle * 10))  # 0–3600
    
    # Store in hardware state
    hardware_state["stepper_angle"] = angle
    
    # Broadcast to all WebSocket clients
    socketio.emit('stepper_update', {'angle': angle, 'source': 'api'}, broadcast=True)

    if ser:
        with serial_lock:
            try:
                packet = bytearray([0xFC, (angle10 >> 8) & 0xFF, angle10 & 0xFF])
                ser.write(packet)
                ser.flush()
                hardware_state["stepper_angle"] = angle
                return jsonify(success=True, angle=angle)
            except Exception as e:
                print(f"Stepper error: {e}")
                return jsonify(success=False, error=str(e)), 500
    return jsonify(success=False, error="Serial connection inactive", angle=angle, simulation=True)

@app.route('/led', methods=['POST'])
def led_control():
    mode = request.json.get('mode')
    cmd_map = {'on': 254, 'off': 253, 'blink': 252}
    cmd = cmd_map.get(mode)
    if cmd is None:
        return jsonify(mode=mode, status="Unknown mode"), 400
    
    # Store in hardware state
    hardware_state["led_status"] = mode
    
    # Broadcast to all WebSocket clients
    socketio.emit('led_update', {'mode': mode, 'source': 'api'}, broadcast=True)
    
    if ser:
        with serial_lock:
            ser.write(bytearray([cmd]))
            ser.flush()
        hardware_state["led_status"] = mode
        return jsonify(mode=mode, status="Command Sent")
    return jsonify(mode=mode, status="Simulation Mode Only")

@app.route('/get_analytics', methods=['GET'])
def get_analytics():
    return jsonify(load_analytics())

@app.route('/update_analytics', methods=['POST'])
def update_analytics():
    data = request.json
    save_analytics(data)
    return jsonify(status="ok")

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)