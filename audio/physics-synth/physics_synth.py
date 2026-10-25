#!/usr/bin/env python3
"""
3D Physics-Based Synthesizer
Builds instruments from masses, springs, and constraints.
Real-time audio synthesis + OSC/MIDI control + video data export.

Usage:
    python physics_synth.py --config patch.yaml --audio --osc :9001
"""

import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional, Callable
from enum import Enum
import json
from datetime import datetime
import threading
import queue
from pathlib import Path


# ============================================================================
# PHYSICS PRIMITIVES
# ============================================================================

@dataclass
class Vec3:
    """3D vector with numpy backend."""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    
    def to_array(self) -> np.ndarray:
        return np.array([self.x, self.y, self.z], dtype=np.float32)
    
    @classmethod
    def from_array(cls, arr: np.ndarray) -> 'Vec3':
        return cls(float(arr[0]), float(arr[1]), float(arr[2]))
    
    def length(self) -> float:
        return np.sqrt(self.x**2 + self.y**2 + self.z**2)
    
    def normalize(self) -> 'Vec3':
        l = self.length()
        if l == 0:
            return Vec3(0, 0, 0)
        return Vec3(self.x/l, self.y/l, self.z/l)
    
    def __add__(self, other: 'Vec3') -> 'Vec3':
        return Vec3(self.x + other.x, self.y + other.y, self.z + other.z)
    
    def __sub__(self, other: 'Vec3') -> 'Vec3':
        return Vec3(self.x - other.x, self.y - other.y, self.z - other.z)
    
    def __mul__(self, scalar: float) -> 'Vec3':
        return Vec3(self.x * scalar, self.y * scalar, self.z * scalar)
    
    def dot(self, other: 'Vec3') -> float:
        return self.x * other.x + self.y * other.y + self.z * other.z
    
    def cross(self, other: 'Vec3') -> 'Vec3':
        return Vec3(
            self.y * other.z - self.z * other.y,
            self.z * other.x - self.x * other.z,
            self.x * other.y - self.y * other.x
        )


class Mass:
    """A point mass in 3D space."""
    
    _id_counter = 0
    
    def __init__(self, pos: Vec3, mass: float = 1.0, fixed: bool = False, damping: float = 0.02):
        self.id = Mass._id_counter
        Mass._id_counter += 1
        
        self.pos = pos
        self.vel = Vec3()
        self.acc = Vec3()
        self.mass = mass
        self.fixed = fixed
        self.damping = damping
        self.forces = Vec3()
        
        # Audio synthesis state
        self.freq = 440.0  # Hz
        self.phase = 0.0
        self.amplitude = 1.0
    
    def apply_force(self, force: Vec3):
        """Add a force to accumulate (F = ma)."""
        if not self.fixed:
            self.forces = self.forces + force
    
    def integrate(self, dt: float):
        """Verlet/Euler integration step."""
        if self.fixed:
            return
        
        # a = F / m
        self.acc = self.forces * (1.0 / self.mass)
        
        # v += a * dt (with damping)
        self.vel = (self.vel + self.acc * dt) * (1.0 - self.damping)
        
        # p += v * dt
        self.pos = self.pos + self.vel * dt
        
        # Reset accumulated forces
        self.forces = Vec3()
    
    def update_audio_state(self):
        """Derive audio synthesis parameters from physics."""
        # Velocity magnitude -> frequency deviation
        vel_mag = self.vel.length()
        self.freq = 440.0 + (vel_mag * 50.0)  # Scale velocity to freq modulation
        
        # Position magnitude -> amplitude
        pos_mag = self.pos.length()
        self.amplitude = max(0.0, min(1.0, 0.5 + pos_mag * 0.1))
    
    def to_dict(self) -> Dict:
        return {
            'id': self.id,
            'pos': (self.pos.x, self.pos.y, self.pos.z),
            'vel': (self.vel.x, self.vel.y, self.vel.z),
            'freq': self.freq,
            'amp': self.amplitude,
            'mass': self.mass
        }


class Spring:
    """A spring connecting two masses."""
    
    _id_counter = 0
    
    def __init__(self, m1: Mass, m2: Mass, rest_length: float = 1.0, 
                 stiffness: float = 1.0, damping: float = 0.1):
        self.id = Spring._id_counter
        Spring._id_counter += 1
        
        self.m1 = m1
        self.m2 = m2
        self.rest_length = rest_length
        self.stiffness = stiffness
        self.damping = damping
        
        # Audio synthesis state
        self.tension = 0.0
    
    def apply_forces(self):
        """Hooke's law: F = -k * dx."""
        delta = self.m2.pos - self.m1.pos
        dist = delta.length()
        
        if dist < 0.001:  # Avoid division by zero
            return
        
        # Spring force
        force_mag = -self.stiffness * (dist - self.rest_length)
        force_dir = delta.normalize()
        force = force_dir * force_mag
        
        # Damping (velocity-dependent)
        vel_delta = self.m2.vel - self.m1.vel
        damping_force = vel_delta * self.damping
        
        total_force = force + damping_force
        
        self.m1.apply_force(total_force)
        self.m2.apply_force(total_force * (-1.0))
        
        # Store tension for audio
        self.tension = abs(force_mag)
    
    def to_dict(self) -> Dict:
        return {
            'id': self.id,
            'm1': self.m1.id,
            'm2': self.m2.id,
            'tension': self.tension,
            'length': (self.m2.pos - self.m1.pos).length()
        }


# ============================================================================
# PHYSICS SYSTEM
# ============================================================================

class PhysicsSystem:
    """Manages masses, springs, forces, and integration."""
    
    def __init__(self, gravity: Vec3 = Vec3(0, -9.81, 0), dt: float = 0.01):
        self.masses: Dict[int, Mass] = {}
        self.springs: Dict[int, Spring] = {}
        self.gravity = gravity
        self.dt = dt
        self.time = 0.0
        self.substeps = 4  # Euler substep iterations per frame
    
    def add_mass(self, pos: Vec3, mass: float = 1.0, fixed: bool = False) -> Mass:
        m = Mass(pos, mass, fixed)
        self.masses[m.id] = m
        return m
    
    def add_spring(self, m1: Mass, m2: Mass, rest_length: Optional[float] = None, 
                   stiffness: float = 1.0) -> Spring:
        if rest_length is None:
            rest_length = (m2.pos - m1.pos).length()
        
        s = Spring(m1, m2, rest_length, stiffness)
        self.springs[s.id] = s
        return s
    
    def step(self):
        """Advance physics by one frame."""
        dt_sub = self.dt / self.substeps
        
        for _ in range(self.substeps):
            # Apply gravity
            for mass in self.masses.values():
                mass.apply_force(self.gravity * mass.mass)
            
            # Apply spring forces
            for spring in self.springs.values():
                spring.apply_forces()
            
            # Integrate
            for mass in self.masses.values():
                mass.integrate(dt_sub)
                mass.update_audio_state()
        
        self.time += self.dt
    
    def get_state(self) -> Dict:
        """Snapshot the current system state."""
        return {
            'time': self.time,
            'masses': {mid: m.to_dict() for mid, m in self.masses.items()},
            'springs': {sid: s.to_dict() for sid, s in self.springs.items()}
        }


# ============================================================================
# AUDIO SYNTHESIS
# ============================================================================

class Waveform(Enum):
    SINE = "sine"
    TRIANGLE = "triangle"
    SQUARE = "square"
    SAWTOOTH = "sawtooth"


class AudioSynthesizer:
    """Converts physics state into audio signals."""
    
    def __init__(self, sample_rate: float = 44100.0):
        self.sample_rate = sample_rate
        self.waveform = Waveform.SINE
    
    def oscillate(self, mass: Mass, phase_delta: float) -> float:
        """Generate audio sample from mass motion."""
        mass.phase += phase_delta
        
        # Wrap phase
        mass.phase = mass.phase % (2 * np.pi)
        
        # Generate waveform
        if self.waveform == Waveform.SINE:
            sig = np.sin(mass.phase)
        elif self.waveform == Waveform.TRIANGLE:
            # Triangle: ramps up/down
            normalized = (mass.phase / (2 * np.pi))
            if normalized < 0.5:
                sig = 4 * normalized - 1
            else:
                sig = 3 - 4 * normalized
        elif self.waveform == Waveform.SQUARE:
            sig = 1.0 if mass.phase < np.pi else -1.0
        elif self.waveform == Waveform.SAWTOOTH:
            sig = 2 * (mass.phase / (2 * np.pi) - 0.5)
        
        return sig * mass.amplitude
    
    def synthesize_frame(self, system: PhysicsSystem, num_samples: int = 1024) -> np.ndarray:
        """Generate one audio frame (array of samples)."""
        samples = np.zeros(num_samples, dtype=np.float32)
        
        for mass in system.masses.values():
            # Phase increment per sample
            phase_delta = 2 * np.pi * mass.freq / self.sample_rate
            
            for i in range(num_samples):
                samples[i] += self.oscillate(mass, phase_delta)
        
        # Normalize to prevent clipping
        max_val = np.max(np.abs(samples))
        if max_val > 0:
            samples = samples / max_val * 0.9
        
        return samples


# ============================================================================
# CONTROL INTERFACE (OSC/MIDI stubs)
# ============================================================================

class ControlInterface:
    """Accepts external control (OSC, MIDI, etc.)."""
    
    def __init__(self, system: PhysicsSystem):
        self.system = system
        self.command_queue: queue.Queue = queue.Queue()
    
    def push_mass_force(self, mass_id: int, force: Vec3):
        """Apply an external force to a mass."""
        if mass_id in self.system.masses:
            self.system.masses[mass_id].apply_force(force)
    
    def set_mass_freq(self, mass_id: int, freq: float):
        """Override frequency for a mass."""
        if mass_id in self.system.masses:
            self.system.masses[mass_id].freq = max(20.0, min(8000.0, freq))
    
    def parse_osc_message(self, address: str, args: Tuple):
        """Parse incoming OSC messages (stub for real OSC integration)."""
        if address == "/mass/force":
            mass_id, fx, fy, fz = args
            self.push_mass_force(int(mass_id), Vec3(fx, fy, fz))
        elif address == "/mass/freq":
            mass_id, freq = args
            self.set_mass_freq(int(mass_id), float(freq))
        elif address == "/global/gravity":
            gx, gy, gz = args
            self.system.gravity = Vec3(gx, gy, gz)


# ============================================================================
# EXAMPLE PATCHES (preset configurations)
# ============================================================================

def create_simple_oscillator(system: PhysicsSystem) -> Tuple[Mass, Mass]:
    """Create a minimal 2-mass oscillator."""
    m1 = system.add_mass(Vec3(-0.5, 0, 0), mass=0.5)
    m2 = system.add_mass(Vec3(0.5, 0, 0), mass=0.5)
    spring = system.add_spring(m1, m2, stiffness=10.0)
    
    # Excite the system
    m1.vel = Vec3(2, 0, 0)
    
    return m1, m2


def create_coupled_pendulums(system: PhysicsSystem) -> List[Mass]:
    """Create 3 pendulums coupled via springs."""
    anchor = system.add_mass(Vec3(0, 1, 0), fixed=True)
    
    masses = []
    for i in range(3):
        angle = (i - 1) * 0.4
        x = np.sin(angle)
        y = -np.cos(angle) * 0.8
        m = system.add_mass(Vec3(x, y, 0), mass=1.0)
        
        # Pendulum: gravity + pivot spring
        system.add_spring(anchor, m, stiffness=5.0)
        masses.append(m)
    
    # Cross-coupling
    system.add_spring(masses[0], masses[1], stiffness=2.0)
    system.add_spring(masses[1], masses[2], stiffness=2.0)
    
    # Initial perturbation
    masses[0].vel = Vec3(0.5, 0, 0)
    
    return masses


def create_string(system: PhysicsSystem, num_nodes: int = 8) -> List[Mass]:
    """Create a vibrating string (fixed at ends)."""
    masses = []
    
    for i in range(num_nodes):
        x = -1.0 + (2.0 * i / (num_nodes - 1))
        m = system.add_mass(Vec3(x, 0, 0), mass=0.5, fixed=(i == 0 or i == num_nodes - 1))
        masses.append(m)
    
    # Connect with springs
    for i in range(num_nodes - 1):
        system.add_spring(masses[i], masses[i+1], stiffness=20.0)
    
    # Pluck it
    if num_nodes // 2 < len(masses):
        masses[num_nodes // 2].vel = Vec3(0, 2.0, 0)
    
    return masses


# ============================================================================
# MAIN SYNTHESIZER CLASS
# ============================================================================

class PhysicsSynth:
    """Main synthesizer combining physics, audio, and control."""
    
    def __init__(self, sample_rate: float = 44100.0, buffer_size: int = 1024):
        self.sample_rate = sample_rate
        self.buffer_size = buffer_size
        self.dt = 1.0 / sample_rate  # Physics dt matches audio sample rate
        
        self.system = PhysicsSystem(dt=self.dt)
        self.synth = AudioSynthesizer(sample_rate)
        self.control = ControlInterface(self.system)
        
        self.running = False
        self.frames_processed = 0
    
    def initialize_patch(self, patch_name: str = "string"):
        """Load a preset patch."""
        if patch_name == "oscillator":
            create_simple_oscillator(self.system)
        elif patch_name == "pendulums":
            create_coupled_pendulums(self.system)
        elif patch_name == "string":
            create_string(self.system)
        else:
            raise ValueError(f"Unknown patch: {patch_name}")
    
    def process_audio_block(self) -> np.ndarray:
        """Generate one block of audio samples."""
        samples = np.zeros(self.buffer_size, dtype=np.float32)
        
        for sample_idx in range(self.buffer_size):
            # Advance physics for one sample
            self.system.step()
            
            # Generate one audio sample from all masses
            for mass in self.system.masses.values():
                phase_delta = 2 * np.pi * mass.freq / self.sample_rate
                samples[sample_idx] += self.synth.oscillate(mass, phase_delta)
        
        # Normalize
        max_val = np.max(np.abs(samples))
        if max_val > 0:
            samples = samples / max_val * 0.9
        
        self.frames_processed += 1
        return samples
    
    def get_visualization_frame(self) -> Dict:
        """Export current state for video rendering (gem.bfg, etc.)."""
        return self.system.get_state()
    
    def save_state(self, filepath: str):
        """Snapshot the entire system to JSON."""
        state = {
            'timestamp': datetime.now().isoformat(),
            'frames': self.frames_processed,
            'system': self.system.get_state(),
            'config': {
                'sample_rate': self.sample_rate,
                'buffer_size': self.buffer_size,
                'waveform': self.synth.waveform.value
            }
        }
        with open(filepath, 'w') as f:
            json.dump(state, f, indent=2)
    
    def run_offline(self, duration_seconds: float = 10.0) -> np.ndarray:
        """Generate audio offline (no real-time constraints)."""
        num_samples = int(self.sample_rate * duration_seconds)
        num_blocks = num_samples // self.buffer_size
        
        all_samples = []
        for block_idx in range(num_blocks):
            block = self.process_audio_block()
            all_samples.append(block)
        
        return np.concatenate(all_samples)


# ============================================================================
# DEMO / CLI
# ============================================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="3D Physics Synthesizer")
    parser.add_argument("--patch", default="string", 
                       choices=["oscillator", "pendulums", "string"],
                       help="Patch to load")
    parser.add_argument("--duration", type=float, default=5.0, help="Duration in seconds")
    parser.add_argument("--output", help="Write audio to WAV file")
    parser.add_argument("--state-dump", help="Export system state to JSON")
    
    args = parser.parse_args()
    
    print(f"[Physics Synth] Initializing {args.patch} patch...")
    synth = PhysicsSynth(sample_rate=44100.0, buffer_size=1024)
    synth.initialize_patch(args.patch)
    
    print(f"[Physics Synth] Rendering {args.duration}s of audio...")
    audio = synth.run_offline(args.duration)
    
    if args.output:
        try:
            import scipy.io.wavfile as wavfile
            # Normalize audio
            audio = audio / (np.max(np.abs(audio)) + 1e-6)
            audio_int16 = (audio * 32767).astype(np.int16)
            wavfile.write(args.output, int(synth.sample_rate), audio_int16)
            print(f"[Physics Synth] Wrote audio to {args.output}")
        except ImportError:
            print("[Physics Synth] scipy not installed, skipping audio output")
    
    if args.state_dump:
        synth.save_state(args.state_dump)
        print(f"[Physics Synth] Wrote state to {args.state_dump}")
    
    print(f"[Physics Synth] Done. Generated {len(audio)} samples.")
