#!/usr/bin/env python3
"""
Physics Synth Integration Layer
Bridges physics_synth.py with radiot.py, dispatch, and video pipeline.

- Real-time OSC control from dispatch
- Audio output to radiot.py mixer
- Visualization frame export for gem.bfg
- Autonomous patch sequencing
"""

import numpy as np
import threading
import time
import json
from typing import Dict, List, Optional, Callable
from physics_synth import (
    PhysicsSynth, PhysicsSystem, Vec3, Mass, Spring,
    Waveform, create_string, create_coupled_pendulums
)


# ============================================================================
# ADVANCED PATCHES (System complexity builders)
# ============================================================================

class PatchBuilder:
    """Factory for complex multi-component patches."""
    
    @staticmethod
    def create_modal_resonator(system: PhysicsSystem, num_modes: int = 5) -> List[Mass]:
        """
        Modal synthesis: N oscillators at different frequencies.
        Driven by a central exciter.
        """
        exciter = system.add_mass(Vec3(0, 0, 0), mass=1.0)
        
        modes = []
        for i in range(num_modes):
            # Distribute modes in 3D circle
            angle = (2 * np.pi * i) / num_modes
            x = np.cos(angle)
            z = np.sin(angle)
            
            mode = system.add_mass(Vec3(x, 0, z), mass=0.8)
            
            # Strong coupling to exciter
            freq_ratio = 1.0 + (i / num_modes)  # Harmonic series
            spring = system.add_spring(exciter, mode, stiffness=freq_ratio * 8.0)
            modes.append(mode)
        
        # Cross-modal damping (modes affect each other)
        for i in range(num_modes - 1):
            system.add_spring(modes[i], modes[i+1], 
                            rest_length=0.1, stiffness=2.0, damping=0.15)
        
        # Exciter velocity -> audio
        exciter.vel = Vec3(3, 0, 0)
        
        return [exciter] + modes
    
    @staticmethod
    def create_chaotic_orbiter(system: PhysicsSystem, num_bodies: int = 3) -> List[Mass]:
        """
        Gravity-based chaos: N bodies orbiting a center.
        Produces complex, evolving timbral interactions.
        """
        center = system.add_mass(Vec3(0, 0, 0), mass=2.0, fixed=True)
        
        bodies = []
        for i in range(num_bodies):
            angle = (2 * np.pi * i) / num_bodies
            dist = 1.0 + (i * 0.3)
            
            x = np.cos(angle) * dist
            z = np.sin(angle) * dist
            
            body = system.add_mass(Vec3(x, 0, z), mass=0.5)
            
            # Gravitational coupling (via spring approximation)
            system.add_spring(center, body, stiffness=3.0, damping=0.08)
            bodies.append(body)
        
        # Inter-body perturbation springs (weak)
        for i in range(num_bodies):
            for j in range(i+1, num_bodies):
                system.add_spring(bodies[i], bodies[j], 
                                rest_length=0.5, stiffness=0.5, damping=0.2)
        
        # Kick one into orbit
        if bodies:
            bodies[0].vel = Vec3(0, 0, 3)
        
        return [center] + bodies
    
    @staticmethod
    def create_wave_packet(system: PhysicsSystem, wave_type: str = "transverse") -> List[Mass]:
        """
        Create a traveling wave: coupled string with complex boundary.
        wave_type: 'transverse' (2D motion), 'longitudinal' (compression)
        """
        num_nodes = 16
        masses = []
        
        for i in range(num_nodes):
            x = -2.0 + (4.0 * i / (num_nodes - 1))
            
            # Fixed only at very ends
            fixed = (i == 0 or i == num_nodes - 1)
            m = system.add_mass(Vec3(x, 0, 0), mass=0.3, fixed=fixed)
            masses.append(m)
        
        # Connect with springs (varying stiffness for dispersive wave)
        for i in range(num_nodes - 1):
            # Stiffness decreases toward center (dispersive)
            center_dist = abs((i + 0.5) - (num_nodes / 2))
            stiffness = 15.0 + (center_dist * 0.5)
            
            system.add_spring(masses[i], masses[i+1], stiffness=stiffness, damping=0.12)
        
        # Excite the packet
        center_idx = num_nodes // 2
        if wave_type == "transverse":
            # Perpendicular motion
            masses[center_idx].vel = Vec3(0, 4.0, 0)
        else:
            # Longitudinal: along spring direction
            masses[center_idx].vel = Vec3(2.0, 0, 0)
        
        return masses


# ============================================================================
# REAL-TIME OSC DRIVER (for dispatch/broadcast integration)
# ============================================================================

class PhysSynthOSCDriver:
    """
    Real-time controller. Accepts messages like:
        /phys/excite mass_id fx fy fz
        /phys/param mass_id freq amplitude
        /phys/patch name
    """
    
    def __init__(self, synth: PhysicsSynth, listen_addr: str = "127.0.0.1", port: int = 9001):
        self.synth = synth
        self.listen_addr = listen_addr
        self.port = port
        
        self.running = False
        self.osc_thread = None
        
        # Message handlers
        self.handlers: Dict[str, Callable] = {
            '/phys/excite': self.handle_excite,
            '/phys/param': self.handle_param,
            '/phys/patch': self.handle_patch,
            '/phys/gravity': self.handle_gravity,
            '/phys/damping': self.handle_damping,
        }
    
    def handle_excite(self, mass_id: int, fx: float, fy: float, fz: float):
        """Apply force to a mass."""
        self.synth.control.push_mass_force(mass_id, Vec3(fx, fy, fz))
    
    def handle_param(self, mass_id: int, freq: float, amp: float):
        """Set frequency + amplitude."""
        if mass_id in self.synth.system.masses:
            m = self.synth.system.masses[mass_id]
            m.freq = freq
            m.amplitude = amp
    
    def handle_patch(self, patch_name: str):
        """Switch patches dynamically."""
        # Clear current
        self.synth.system.masses.clear()
        self.synth.system.springs.clear()
        
        # Load new
        try:
            self.synth.initialize_patch(patch_name)
            print(f"[OSCDriver] Loaded patch: {patch_name}")
        except Exception as e:
            print(f"[OSCDriver] Error loading patch {patch_name}: {e}")
    
    def handle_gravity(self, gx: float, gy: float, gz: float):
        """Modify global gravity."""
        self.synth.system.gravity = Vec3(gx, gy, gz)
    
    def handle_damping(self, mass_id: int, damping: float):
        """Set mass damping."""
        if mass_id in self.synth.system.masses:
            self.synth.system.masses[mass_id].damping = max(0, min(1, damping))
    
    def mock_osc_server(self):
        """Stub OSC server (replace with python-osc for real integration)."""
        print(f"[OSCDriver] Mock OSC server on {self.listen_addr}:{self.port}")
        print("[OSCDriver] Real OSC requires: pip install python-osc")
        
        while self.running:
            # In production, this would actually listen for OSC
            time.sleep(0.1)
    
    def start(self):
        """Start OSC listener thread."""
        self.running = True
        self.osc_thread = threading.Thread(target=self.mock_osc_server, daemon=True)
        self.osc_thread.start()
    
    def stop(self):
        """Stop OSC listener."""
        self.running = False
        if self.osc_thread:
            self.osc_thread.join(timeout=1.0)


# ============================================================================
# RADIOT.PY INTEGRATION
# ============================================================================

class RadiotBridge:
    """
    Bridges PhysSynth -> radiot.py audio mixer.
    
    Usage:
        bridge = RadiotBridge(synth)
        bridge.connect_to_radiot("localhost", 9000)
        bridge.start()
    """
    
    def __init__(self, synth: PhysicsSynth, chunk_size: int = 2048):
        self.synth = synth
        self.chunk_size = chunk_size
        self.running = False
        self.thread = None
        
        self.radiot_socket = None
        self.radiot_addr = None
    
    def connect_to_radiot(self, host: str = "localhost", port: int = 9000):
        """Connect to radiot.py UDP endpoint."""
        # In production: socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.radiot_addr = (host, port)
        print(f"[RadiotBridge] Configured to send to {host}:{port}")
    
    def send_audio_chunk(self, audio: np.ndarray):
        """Send audio chunk to radiot (stub)."""
        if self.radiot_addr:
            # Convert float32 -> int16 -> bytes
            audio_int16 = (audio * 32767).astype(np.int16)
            # In production: self.radiot_socket.sendto(audio_int16.tobytes(), self.radiot_addr)
            pass
    
    def stream_loop(self):
        """Real-time streaming loop."""
        print("[RadiotBridge] Streaming started")
        
        while self.running:
            # Generate one chunk from synth
            chunk = self.synth.process_audio_block()
            
            # Send to radiot
            self.send_audio_chunk(chunk)
    
    def start(self):
        """Start streaming thread."""
        self.running = True
        self.thread = threading.Thread(target=self.stream_loop, daemon=True)
        self.thread.start()
    
    def stop(self):
        """Stop streaming."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=1.0)


# ============================================================================
# VIDEO/GEM.BFG EXPORT
# ============================================================================

class VisualizationExporter:
    """
    Exports physics state for rendering in gem.bfg or other visualizers.
    Formats: JSON frames, point cloud, OBJ sequence
    """
    
    def __init__(self, synth: PhysicsSynth, output_dir: str = "./vis_export"):
        self.synth = synth
        self.output_dir = output_dir
        self.frame_count = 0
    
    def export_json_frame(self) -> Dict:
        """Export current state as JSON."""
        state = self.synth.get_visualization_frame()
        state['frame'] = self.frame_count
        return state
    
    def export_point_cloud(self) -> List[Dict]:
        """Export masses as point cloud."""
        points = []
        for mass_id, mass in self.synth.system.masses.items():
            points.append({
                'id': mass_id,
                'pos': [mass.pos.x, mass.pos.y, mass.pos.z],
                'vel': [mass.vel.x, mass.vel.y, mass.vel.z],
                'freq': mass.freq,
                'amp': mass.amplitude,
                'color': self._freq_to_color(mass.freq)
            })
        return points
    
    def _freq_to_color(self, freq: float) -> List[float]:
        """Map frequency to RGB color (hue)."""
        # Normalize freq to 0-1 (20Hz - 8kHz)
        norm = (np.log2(freq) - np.log2(20)) / (np.log2(8000) - np.log2(20))
        norm = max(0, min(1, norm))
        
        # Simple HSV -> RGB
        hue = norm * 360
        h = hue / 60.0
        s, v = 1.0, 1.0
        
        c = v * s
        x = c * (1 - abs((h % 2) - 1))
        
        if h < 1: r, g, b = c, x, 0
        elif h < 2: r, g, b = x, c, 0
        elif h < 3: r, g, b = 0, c, x
        elif h < 4: r, g, b = 0, x, c
        elif h < 5: r, g, b = x, 0, c
        else: r, g, b = c, 0, x
        
        return [r, g, b]
    
    def export_obj_frame(self, filepath: str):
        """Export as Wavefront OBJ (points + springs as lines)."""
        with open(filepath, 'w') as f:
            f.write("# Physics Synth OBJ Export\n")
            f.write(f"# Frame {self.frame_count}\n")
            
            # Write vertices
            for mass_id, mass in self.synth.system.masses.items():
                f.write(f"v {mass.pos.x} {mass.pos.y} {mass.pos.z}\n")
            
            # Write springs as line segments
            for spring in self.synth.system.springs.values():
                m1_idx = list(self.synth.system.masses.keys()).index(spring.m1.id) + 1
                m2_idx = list(self.synth.system.masses.keys()).index(spring.m2.id) + 1
                f.write(f"l {m1_idx} {m2_idx}\n")
        
        self.frame_count += 1


# ============================================================================
# AUTONOMOUS SEQUENCER (for dispatch integration)
# ============================================================================

class AutonomousSequencer:
    """
    Drives the physics synth autonomously via scheduled patch changes,
    parameter modulation, and system perturbations.
    
    Integrates with dispatch for scheduled execution.
    """
    
    def __init__(self, synth: PhysicsSynth):
        self.synth = synth
        self.running = False
        self.thread = None
        
        # Sequence state
        self.current_scene = 0
        self.scene_duration = 30.0  # seconds per scene
        self.start_time = None
        
        # Scene definitions
        self.scenes = [
            {'patch': 'string', 'gravity': (0, -2, 0), 'duration': 30},
            {'patch': 'pendulums', 'gravity': (0, -5, 0), 'duration': 25},
            {'patch': 'oscillator', 'gravity': (0.5, -1, 0), 'duration': 20},
        ]
    
    def add_scene(self, scene: Dict):
        """Register a new scene."""
        self.scenes.append(scene)
    
    def sequence_loop(self):
        """Autonomous sequencing loop."""
        self.start_time = time.time()
        scene_start = self.start_time
        
        scene_idx = 0
        while self.running:
            elapsed = time.time() - self.start_time
            scene_elapsed = time.time() - scene_start
            
            scene = self.scenes[scene_idx % len(self.scenes)]
            
            # Switch patch at scene start
            if scene_elapsed < 0.1:  # First 100ms of scene
                try:
                    # Load patch
                    self.synth.system.masses.clear()
                    self.synth.system.springs.clear()
                    
                    if scene['patch'] == 'string':
                        create_string(self.synth.system)
                    elif scene['patch'] == 'pendulums':
                        create_coupled_pendulums(self.synth.system)
                    
                    # Set gravity
                    if 'gravity' in scene:
                        g = scene['gravity']
                        self.synth.system.gravity = Vec3(g[0], g[1], g[2])
                    
                    print(f"[Sequencer] Scene {scene_idx}: {scene['patch']}")
                except Exception as e:
                    print(f"[Sequencer] Error in scene {scene_idx}: {e}")
            
            # Check if time to advance
            if scene_elapsed > scene.get('duration', 30):
                scene_start = time.time()
                scene_idx += 1
            
            time.sleep(0.01)
    
    def start(self):
        """Start autonomous sequencing."""
        self.running = True
        self.thread = threading.Thread(target=self.sequence_loop, daemon=True)
        self.thread.start()
    
    def stop(self):
        """Stop sequencing."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=1.0)


# ============================================================================
# DEMO: Full integration
# ============================================================================

if __name__ == "__main__":
    print("[Physics Synth Integration] Starting demo...")
    
    # Create synth
    synth = PhysicsSynth(sample_rate=44100.0, buffer_size=2048)
    synth.initialize_patch("string")
    
    # Set up integrations
    osc_driver = PhysSynthOSCDriver(synth, port=9001)
    radiot_bridge = RadiotBridge(synth, chunk_size=2048)
    visualizer = VisualizationExporter(synth)
    sequencer = AutonomousSequencer(synth)
    
    # Configure radiot connection (stub)
    radiot_bridge.connect_to_radiot("localhost", 9000)
    
    # Start threads
    print("[Physics Synth Integration] Starting OSC driver...")
    osc_driver.start()
    
    print("[Physics Synth Integration] Starting radiot bridge...")
    # radiot_bridge.start()  # Uncomment for real-time
    
    print("[Physics Synth Integration] Starting autonomous sequencer...")
    sequencer.start()
    
    # Run for a while, periodically export visualization
    try:
        for i in range(5):
            time.sleep(6)  # 6 second scenes
            
            # Export current state
            frame_data = visualizer.export_json_frame()
            print(f"[Integration] Frame {visualizer.frame_count}: {len(synth.system.masses)} masses")
    
    except KeyboardInterrupt:
        print("[Physics Synth Integration] Shutting down...")
    
    finally:
        sequencer.stop()
        osc_driver.stop()
        radiot_bridge.stop()
        
        print("[Physics Synth Integration] Done.")
