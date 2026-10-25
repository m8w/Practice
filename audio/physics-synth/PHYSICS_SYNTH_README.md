# Physics Synth: 3D Physics-Based Synthesizer

A Python synthesizer that builds instruments from simulated masses, springs, and forces. Real-time audio synthesis with integration hooks for your existing broadcast/generative systems.

**Core Idea**: Physics → Sound. Motion generates audio.

---

## Quick Start

### 1. Basic Offline Generation

```bash
# Generate 5 seconds of vibrating string audio
python physics_synth.py --patch string --duration 5 --output string.wav

# Try the pendulum patch
python physics_synth.py --patch pendulums --duration 10 --output pendulum.wav

# Or the simple oscillator
python physics_synth.py --patch oscillator --duration 3 --output osc.wav
```

### 2. Programmatic Usage

```python
from physics_synth import PhysicsSynth, Vec3

# Create synth
synth = PhysicsSynth(sample_rate=44100.0, buffer_size=1024)

# Load a preset patch
synth.initialize_patch("string")

# Generate audio
audio = synth.run_offline(duration=10.0)

# Or process in real-time blocks
for _ in range(1000):  # 1000 blocks
    block = synth.process_audio_block()  # np.ndarray, shape (1024,)
    # Send to radiot.py, write to file, etc.
```

### 3. Build Custom Patches

```python
from physics_synth import PhysicsSystem, Vec3

system = PhysicsSystem(gravity=Vec3(0, -5, 0))

# Create two masses
m1 = system.add_mass(Vec3(-0.5, 0, 0), mass=1.0)
m2 = system.add_mass(Vec3(0.5, 0, 0), mass=1.0)

# Connect with spring
spring = system.add_spring(m1, m2, stiffness=10.0)

# Excite
m1.vel = Vec3(3, 0, 0)

# Step the physics
for _ in range(10000):
    system.step()
```

---

## Architecture

### Core Classes (physics_synth.py)

- **Vec3**: 3D vector math (position, velocity, force)
- **Mass**: Point mass with position, velocity, acceleration, forces
- **Spring**: Hooke's law connector between masses
- **PhysicsSystem**: Integrates masses and springs over time
- **AudioSynthesizer**: Converts physics state → audio samples
- **PhysicsSynth**: Main wrapper combining physics + audio
- **ControlInterface**: Receives external commands (OSC, MIDI stubs)

### Integration Layers (physics_synth_integration.py)

- **PatchBuilder**: Factory for complex patches (modal resonators, chaotic orbiters, wave packets)
- **PhysSynthOSCDriver**: Real-time control via OSC (stubs for python-osc)
- **RadiotBridge**: Stream audio to your radiot.py mixer
- **VisualizationExporter**: Export state as JSON/OBJ for gem.bfg or other renderers
- **AutonomousSequencer**: Drive patches autonomously, suitable for dispatch integration

---

## Audio Synthesis Details

### How Physics Becomes Sound

Each mass has:
- **Frequency**: derived from velocity magnitude (motion → pitch)
- **Amplitude**: derived from position magnitude (displacement → volume)
- **Phase**: continuously advanced by frequency during oscillation

The synth generates audio by:
1. Stepping physics (all forces, masses, springs)
2. For each mass: generate one sample of its oscillator
3. Sum all samples
4. Normalize to prevent clipping

```
vel_magnitude -> freq_modulation (e.g., vel * 50 Hz offset)
pos_magnitude -> amplitude (e.g., pos * 0.1 multiplier)
oscillate(freq, phase) -> audio sample
```

### Waveforms

- **SINE** (default): smooth tones
- **TRIANGLE**: bright, nasal
- **SQUARE**: hollow, hollow
- **SAWTOOTH**: buzzy, harsh

Switch waveform:
```python
synth.synth.waveform = Waveform.SAWTOOTH
```

---

## Preset Patches

### 1. String (Default)

A vibrating string with fixed ends. Excited at the center.

```
-----●-----●-----●-----●-----●-----●-----●-----●-----
      ↑ pluck here
```

Characteristics: Rich harmonic content, decaying sustain, natural overtones.

**Usage**:
```python
synth.initialize_patch("string")
```

### 2. Pendulums

Three coupled pendulums hanging from an anchor. Chaotic but periodic motion.

```
        ◆ (anchor)
       /|\
      / | \
     ●  ●  ●  (three pendulums)
```

Characteristics: Percussive transients, complex timbre, period doubling.

**Usage**:
```python
synth.initialize_patch("pendulums")
```

### 3. Oscillator

Two masses connected by a spring, bouncing back and forth.

```
●━━━━━●  (spring oscillating horizontally)
```

Characteristics: Pure, simple tone, sine-like.

**Usage**:
```python
synth.initialize_patch("oscillator")
```

### 4. Modal Resonator (Advanced)

5+ modes driven by a central exciter. Each mode oscillates at a different frequency.

```
        ●
       /|\\\
      / | \ \
     ●  ●  ● ● (modes)
```

**Usage**:
```python
from physics_synth_integration import PatchBuilder
system = PhysicsSystem()
PatchBuilder.create_modal_resonator(system, num_modes=7)
```

### 5. Chaotic Orbiter (Advanced)

N bodies orbiting a central mass, perturbing each other. Produces evolving, unpredictable timbral content.

```
        ●
       /|\
      / | \
     ●  ●  ● (orbiting with cross-coupling)
```

**Usage**:
```python
from physics_synth_integration import PatchBuilder
system = PhysicsSystem()
PatchBuilder.create_chaotic_orbiter(system, num_bodies=4)
```

### 6. Wave Packet (Advanced)

A dispersive traveling wave on a long string. Couples to gem.bfg well.

**Usage**:
```python
from physics_synth_integration import PatchBuilder
system = PhysicsSystem()
PatchBuilder.create_wave_packet(system, wave_type="transverse")
```

---

## Integration with Your Existing Tools

### radiot.py (Audio Mixer)

```python
from physics_synth_integration import RadiotBridge

synth = PhysicsSynth()
synth.initialize_patch("string")

bridge = RadiotBridge(synth, chunk_size=2048)
bridge.connect_to_radiot("localhost", 9000)
bridge.start()

# Generates audio blocks, sends to radiot UDP endpoint
# Keep running while synth.running == True
```

### dispatch (Autonomous Broadcast)

```python
from physics_synth_integration import AutonomousSequencer

synth = PhysicsSynth()

sequencer = AutonomousSequencer(synth)

# Define scenes
sequencer.add_scene({
    'patch': 'string',
    'gravity': (0, -2, 0),
    'duration': 30  # seconds
})
sequencer.add_scene({
    'patch': 'pendulums',
    'gravity': (0, -5, 0),
    'duration': 25
})

sequencer.start()
# Runs autonomously, switches patches on schedule
```

### gem.bfg (Video Rendering)

Export visualization frames as JSON:

```python
from physics_synth_integration import VisualizationExporter

synth = PhysicsSynth()
synth.initialize_patch("string")

visualizer = VisualizationExporter(synth, output_dir="./frames")

for i in range(100):
    synth.process_audio_block()
    
    # Every 10 blocks, export
    if i % 10 == 0:
        frame = visualizer.export_json_frame()
        # frame = {'time': ..., 'masses': {...}, 'springs': {...}}
        
        # Render this in gem.bfg:
        # - masses as glyphs/spheres, color by frequency
        # - springs as lines, thickness by tension
        
        points = visualizer.export_point_cloud()
        # Use this for direct Point Cloud rendering
```

### OSC Control (Dispatch Integration)

```python
from physics_synth_integration import PhysSynthOSCDriver

synth = PhysicsSynth()
osc_driver = PhysSynthOSCDriver(synth, port=9001)
osc_driver.start()

# From dispatch or external OSC controller:
# /phys/excite 0 5.0 0 0     <- Apply 5N force to mass 0 in X
# /phys/param 0 880 0.8      <- Set mass 0 to 880 Hz, 0.8 amplitude
# /phys/patch pendulums      <- Switch to pendulums patch
# /phys/gravity 0 -2 0.5     <- Change gravity
```

---

## API Reference

### PhysicsSystem

```python
system = PhysicsSystem(gravity=Vec3(0, -9.81, 0), dt=0.01)

# Add masses
m1 = system.add_mass(Vec3(0, 0, 0), mass=1.0, fixed=False, damping=0.02)

# Add springs
spring = system.add_spring(m1, m2, rest_length=1.0, stiffness=10.0)

# Step
system.step()

# Inspect
state = system.get_state()
# -> {'time': 0.01, 'masses': {...}, 'springs': {...}}
```

### Mass

```python
mass.apply_force(Vec3(1, 0, 0))  # Add force
mass.pos                          # Current position (Vec3)
mass.vel                          # Current velocity (Vec3)
mass.freq                         # Audio frequency (Hz)
mass.amplitude                    # Audio amplitude (0-1)
mass.to_dict()                    # Serialize
```

### PhysicsSynth

```python
synth = PhysicsSynth(sample_rate=44100.0, buffer_size=1024)

synth.initialize_patch("string")
synth.process_audio_block()       # np.ndarray(1024,), float32

synth.run_offline(10.0)           # Generate 10s, return full array

synth.system                      # Access underlying PhysicsSystem
synth.control                     # Access ControlInterface

synth.get_visualization_frame()   # State snapshot
synth.save_state("snap.json")     # Full system snapshot
```

---

## Performance Notes

- **Physics Timestep**: Matched to audio sample rate (1/44100 = ~0.02ms)
- **CPU**: ~5-10% on modern CPU for 10 masses, string patch
- **Real-time Safe**: Yes, if buffer size >= (sample_rate / 100)
- **Latency**: ~100ms (buffer size / sample rate)

### Optimization Tips

1. **Reduce masses**: Fewer masses = faster physics
2. **Increase damping**: Higher damping stops motion faster
3. **Reduce springs**: Fewer springs = simpler interactions
4. **Use fixed masses**: Fixed masses skip velocity/acceleration updates
5. **Profile**: Use `cProfile` to find bottlenecks

```bash
python -m cProfile -s cumulative physics_synth.py --patch string --duration 5
```

---

## Advanced: Custom Patches

```python
from physics_synth import PhysicsSystem, Vec3, Waveform

# Create system
system = PhysicsSystem(gravity=Vec3(0, -2, 0))

# Build custom patch: 4-body tetrahedral structure
masses = []
for i, pos in enumerate([(0,1,0), (1,-1,0), (-1,-1,0), (0,0,2)]):
    m = system.add_mass(Vec3(*pos), mass=0.5)
    masses.append(m)

# Connect all pairs
for i in range(len(masses)):
    for j in range(i+1, len(masses)):
        system.add_spring(masses[i], masses[j], stiffness=5.0)

# Excite corners
masses[0].vel = Vec3(2, 0, 0)
masses[3].vel = Vec3(-1, 1, -1)

# Use
synth = PhysicsSynth()
synth.system = system

audio = synth.run_offline(5.0)
```

---

## Troubleshooting

### Audio is silent
- Check that masses have non-zero velocity/amplitude
- Increase mass damping -> lower (0.02 is good start)
- Verify oscillator is running: `mass.freq` should not be 440 constantly

### Audio clips/distorts
- Normalize the output (the synth does this automatically)
- Reduce spring stiffness
- Increase mass damping

### System "explodes" (values go to infinity)
- Reduce stiffness (start with 1.0-5.0)
- Increase damping (0.1-0.5)
- Reduce time step (dt=0.001 instead of 0.01)
- Check for disconnected masses (should be connected to fixed anchor)

### Can't integrate with radiot.py
- Verify UDP endpoint is correct
- Check firewall rules
- Start with mock mode (no actual UDP send)

---

## Philosophy

Physics synths embody **emergence**: complex, evolving timbral behaviors arise from simple rules (Hooke's law, Newton's second law). Unlike traditional synthesis (oscillators + envelopes), physics synths are:

- **Deterministic**: Same initial conditions = same sound
- **Continuous**: Endlessly evolving, no loops or repetition
- **Temporal**: Sound IS the motion; no synthesis stage separate from physics
- **Non-linear**: Small changes → wildly different outcomes (chaos)

This fits your asemic / non-linear temporal practice perfectly.

---

## Next Steps

1. **Try presets**: `python physics_synth.py --patch string`
2. **Listen**: Variations in stiffness, damping, gravity all change the sound
3. **Visualize**: Export frames, render in gem.bfg
4. **Integrate**: Hook into dispatch for autonomous sequences
5. **Extend**: Build custom patches, add constraints, explore parameter space

---

## Dependencies

**Required**:
- `numpy` (physics math)

**Optional**:
- `scipy` (for WAV file output)
- `python-osc` (for real OSC control instead of stubs)

Install:
```bash
pip install numpy scipy python-osc
```

---

## File Structure

```
physics_synth.py                 # Core engine
physics_synth_integration.py     # Integration layers
PHYSICS_SYNTH_README.md          # This file
```

---

**Build date**: September 2026  
**Status**: Ready for integration into broadcast/generative workflows
